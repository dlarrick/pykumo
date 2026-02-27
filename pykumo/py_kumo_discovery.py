"""Local network discovery for Kumo WiFi adapters.

Given candidate IP addresses (from HA DHCP discovery) and device credentials
(from the V3 API), probes each IP with each device's credentials to determine
which serial lives at which address.

The Kumo WiFi adapters expose an HTTP API on port 80. A status query
authenticated with the correct password + cryptoSerial returns valid JSON,
confirming the serial-to-IP match. Wrong credentials return an error or empty
response.
"""

import base64
import hashlib
import logging
from typing import Dict, List

import requests

from .const import W_PARAM, S_PARAM

_LOGGER = logging.getLogger(__name__)

# Minimal status query for probing
_PROBE_QUERY = b'{"c":{"indoorUnit":{"status":{}}}}'


def probe_candidate_ips(devices: Dict[str, dict],
                        candidate_ips: List[str],
                        timeout: float = 3.0) -> Dict[str, str]:
    """Probe candidate IPs to match device serials to addresses.

    For each candidate IP, tries authenticating with each unmatched device's
    credentials. A successful JSON response confirms the match.

    Args:
        devices: Dict of serial -> {password, cryptoSerial, ...} from V3 API.
                 password is base64-encoded, cryptoSerial is a hex string.
        candidate_ips: List of IP addresses to probe (e.g. from DHCP discovery).
        timeout: HTTP request timeout in seconds.

    Returns:
        Dict mapping serial -> IP address for successfully matched devices.
    """
    if not devices or not candidate_ips:
        return {}

    # Build decoded credentials for each device
    device_creds = {}
    for serial, dev in devices.items():
        pw_b64 = dev.get("password", "")
        crypto_hex = dev.get("cryptoSerial", "")
        if not pw_b64 or not crypto_hex:
            _LOGGER.debug("Skipping %s: missing credentials", serial)
            continue
        try:
            device_creds[serial] = {
                "password": base64.b64decode(pw_b64),
                "crypto_serial": bytearray.fromhex(crypto_hex),
            }
        except Exception as ex:
            _LOGGER.debug("Skipping %s: bad credentials: %s", serial, ex)

    if not device_creds:
        return {}

    _LOGGER.info("Probing %d candidate IPs for %d devices",
                 len(candidate_ips), len(device_creds))

    result = {}
    unmatched_serials = set(device_creds.keys())

    for ip in candidate_ips:
        if not unmatched_serials:
            break

        for serial in list(unmatched_serials):
            creds = device_creds[serial]
            if _probe_ip(ip, creds, timeout):
                result[serial] = ip
                unmatched_serials.discard(serial)
                _LOGGER.info("Matched %s -> %s", serial, ip)
                break  # This IP is claimed; move to next IP

    if unmatched_serials:
        _LOGGER.warning("Could not match %d/%d devices: %s",
                        len(unmatched_serials), len(device_creds),
                        list(unmatched_serials))
    else:
        _LOGGER.info("All %d devices matched to IPs", len(result))

    return result


def _compute_token(password: bytes, crypto_serial: bytearray,
                   post_data: bytes) -> str:
    """Compute the auth token for a local API request.

    Replicates PyKumoBase._token() without needing a full instance.
    """
    data_hash = hashlib.sha256(password + post_data).digest()

    intermediate = bytearray(88)
    intermediate[0:32] = W_PARAM[0:32]
    intermediate[32:64] = data_hash[0:32]
    intermediate[64:66] = bytearray.fromhex("0840")
    intermediate[66] = S_PARAM
    intermediate[79] = crypto_serial[8]
    intermediate[80:84] = crypto_serial[4:8]
    intermediate[84:88] = crypto_serial[0:4]

    return hashlib.sha256(intermediate).hexdigest()


def _probe_ip(ip: str, creds: dict, timeout: float) -> bool:
    """Try a status query against an IP with given credentials.

    Returns True if the device responds with valid JSON containing expected
    fields (meaning the credentials match this device).
    """
    url = f"http://{ip}/api"
    token = _compute_token(creds["password"], creds["crypto_serial"],
                           _PROBE_QUERY)
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.put(
            url, headers=headers, data=_PROBE_QUERY,
            params={"m": token}, timeout=timeout,
        )
        if resp.ok:
            data = resp.json()
            # A valid response has an "r" key with adapter/indoor unit data
            if isinstance(data, dict) and "r" in data:
                return True
    except Exception:
        pass
    return False
