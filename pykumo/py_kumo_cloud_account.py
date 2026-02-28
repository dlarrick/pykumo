""" Class used to represent the Kumo Cloud service
"""

import json
import logging
import re
import requests
from requests.exceptions import Timeout
from getpass import getpass
from .py_kumo import PyKumo
from .py_kumo_station import PyKumoStation
from .py_kumo_cloud_account_v3 import KumoCloudV3
from .py_kumo_discovery import probe_candidate_ips
from .const import KUMO_CONNECT_TIMEOUT_SECONDS, KUMO_RESPONSE_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

KUMO_UNIT_TYPE_TO_CLASS = {
    "ductless": PyKumo,
    "mvz": PyKumo,
    "sez": PyKumo,
    "pead": PyKumo,
    "headless": PyKumoStation
}


class KumoCloudAccount:
    """ API to talk to KumoCloud servers
    """
    def __init__(self, username, password, kumo_dict=None):
        """ Constructor from URL
        """
        self._username = username
        self._password = password
        self._units = {}
        if kumo_dict:
            self._url = None
            self._kumo_dict = kumo_dict
            self._need_fetch = False
        else:
            self._url = "https://geo-c.kumocloud.com/login"
            self._kumo_dict = None
            self._need_fetch = True

    @staticmethod
    def Factory(username=None, password=None):
        """Factory that prompts for username/password if not given
        """
        if username is None:
            username = input('Kumo Cloud username: ')
        if password is None:
            password = getpass()

        return KumoCloudAccount(username, password)

    @staticmethod
    def _parse_unit(raw_unit):
        """ Parse needed fields from raw json and return dict representing
            a unit
        """
        fields = {'serial', 'label', 'address', 'password', 'cryptoSerial', 'mac', 'unitType'}
        # Not all fields are always present; e.g. 'address'
        common_fields = fields & raw_unit.keys()
        return {field: raw_unit[field] for field in common_fields}

    def _fetch_if_needed(self):
        """ Fetch configuration from server.
        """
        if self._url and self._need_fetch:
            headers = {'Accept': 'application/json, text/plain, */*',
                       'Accept-Encoding': 'gzip, deflate, br',
                       'Accept-Language': 'en-US,en',
                       'Content-Type': 'application/json'}

            body = {"username": self._username,
                    "password": self._password,
                    "appVersion": "2.2.0"}
            try:
                response = requests.post(self._url, headers=headers, data=json.dumps(body),
                                         timeout=(KUMO_CONNECT_TIMEOUT_SECONDS,
                                                  KUMO_RESPONSE_TIMEOUT_SECONDS))
            except Timeout as ex:
                response = None
                _LOGGER.warning("Timeout querying KumoCloud: %s", str(ex))
            if response:
                if response.ok:
                    self._kumo_dict = response.json()
                else:
                    _LOGGER.warning("Error response from KumoCloud: %s %s}",
                                    str(response.status_code), response.text)
            # Only try to fetch once
            self._need_fetch = False
            if not self._kumo_dict:
                _LOGGER.warning("No JSON returned from KumoCloud; check credentials?")
                return

        self._units = {}
        try:
            for child in self._kumo_dict[2]['children']:
                for raw_unit in child['zoneTable'].values():
                    unit = self._parse_unit(raw_unit)
                    serial = unit['serial']
                    self._units[serial] = unit
                if 'children' in child:
                    for grandchild in child['children']:
                        for raw_unit in grandchild['zoneTable'].values():
                            unit = self._parse_unit(raw_unit)
                            serial = unit['serial']
                            self._units[serial] = unit
        except KeyError:
            pass

        # After V2 fetch, supplement missing credentials from V3 API
        if self._username and self._password:
            self._supplement_with_v3()

    def _supplement_with_v3(self):
        """Use the V3 API to fill in missing credentials (password, cryptoSerial).

        After the Kumo Cloud migration, the V2 API may return stale or missing
        passwords. The V3 API provides current data via REST and WebSocket.
        """
        missing = [s for s, u in self._units.items()
                   if not u.get('password') or not u.get('cryptoSerial')]
        if not missing:
            _LOGGER.debug("All V2 credentials complete; skipping V3 supplement")
            return

        _LOGGER.info("V2 data incomplete for %d units, trying V3 API...", len(missing))

        try:
            v3 = KumoCloudV3(self._username, self._password)
            v3_devices = v3.get_all_device_credentials()
        except Exception as ex:
            _LOGGER.warning("V3 credential retrieval failed: %s", ex)
            return

        if not v3_devices:
            return

        # Merge V3 data into units and raw dict
        updated = 0
        for serial, unit in self._units.items():
            v3_dev = v3_devices.get(serial)
            if not v3_dev:
                continue
            for field in ('password', 'cryptoSerial'):
                if not unit.get(field) and v3_dev.get(field):
                    unit[field] = v3_dev[field]
                    updated += 1

        if updated > 0 and self._kumo_dict:
            self._update_raw_dict_credentials(v3_devices)

        _LOGGER.info("V3 supplement: updated %d fields", updated)

    def _update_raw_dict_credentials(self, v3_devices):
        """Update raw kumo_dict with V3-sourced credentials for cache writes."""
        try:
            for child in self._kumo_dict[2]['children']:
                for serial, raw_unit in child['zoneTable'].items():
                    self._merge_v3_fields(raw_unit, v3_devices.get(serial))
                for grandchild in child.get('children', []):
                    for serial, raw_unit in grandchild['zoneTable'].items():
                        self._merge_v3_fields(raw_unit, v3_devices.get(serial))
        except (KeyError, IndexError):
            pass

    @staticmethod
    def _merge_v3_fields(raw_unit, v3_dev):
        """Merge password/cryptoSerial from V3 into a raw unit dict."""
        if not v3_dev:
            return
        for field in ('password', 'cryptoSerial'):
            if v3_dev.get(field) and not raw_unit.get(field):
                raw_unit[field] = v3_dev[field]

    def try_setup(self):
        """Try to set up and return success/failure"""
        self._fetch_if_needed()

        return len(self._units.keys()) > 0

    def try_setup_v3_only(self, candidate_ips=None):
        """Set up using only the V3 API (skip V2 entirely).

        Useful when V2 API is broken for an account. Builds a minimal
        V2-compatible kumo_dict from V3 data, preserving any device
        addresses from a previously cached kumo_dict.

        Args:
            candidate_ips: Optional dict of {mac_address: ip_address} from
                          DHCP discovery. Used to auto-discover device IPs
                          via credential probing.
        """
        if not self._username or not self._password:
            _LOGGER.warning("Cannot use V3-only setup without credentials")
            return False

        # Preserve cached addresses before overwriting _kumo_dict
        cached_addresses = self._extract_cached_addresses()

        try:
            v3 = KumoCloudV3(self._username, self._password)
            v3_devices = v3.get_all_device_credentials()
        except Exception as ex:
            _LOGGER.warning("V3-only setup failed: %s", ex)
            return False

        if not v3_devices:
            return False

        # Build V2-compatible kumo_dict from V3 data
        zone_table = {}
        for serial, dev in v3_devices.items():
            entry = {
                "serial": serial,
                "label": dev.get("label", ""),
                "password": dev.get("password", ""),
                "cryptoSerial": dev.get("cryptoSerial", ""),
                "mac": dev.get("mac", ""),
                "unitType": dev.get("unitType", "ductless"),
            }
            if serial in cached_addresses:
                entry["address"] = cached_addresses[serial]
            zone_table[serial] = entry

        # Auto-discover IPs for units missing addresses via credential probing
        missing = {s: e for s, e in zone_table.items() if not e.get("address")}
        if missing and candidate_ips:
            ips_to_probe = list(candidate_ips.values())
            ip_to_mac = {ip: mac for mac, ip in candidate_ips.items()}
            _LOGGER.info("Probing %d DHCP IPs for %d unaddressed devices",
                         len(ips_to_probe), len(missing))
            matched = probe_candidate_ips(missing, ips_to_probe)
            for serial, ip in matched.items():
                zone_table[serial]["address"] = ip
                # Also populate the MAC from the DHCP mapping
                mac = ip_to_mac.get(ip, "")
                if mac:
                    zone_table[serial]["mac"] = mac
                _LOGGER.info("Discovered %s -> %s (mac=%s)",
                             serial, ip, mac)

        self._kumo_dict = [
            {},  # account info placeholder
            {},  # preferences placeholder
            {"children": [{"zoneTable": zone_table}]},
        ]
        self._need_fetch = False

        self._units = {}
        for serial, unit_data in zone_table.items():
            self._units[serial] = self._parse_unit(unit_data)

        _LOGGER.info("V3-only setup: found %d devices", len(self._units))
        return len(self._units) > 0

    def _extract_cached_addresses(self) -> dict:
        """Extract device serial -> address mapping from current kumo_dict."""
        addresses = {}
        if not self._kumo_dict:
            return addresses
        try:
            for child in self._kumo_dict[2]['children']:
                for raw_unit in child['zoneTable'].values():
                    serial = raw_unit.get('serial', '')
                    address = raw_unit.get('address', '')
                    if serial and address:
                        addresses[serial] = address
                for grandchild in child.get('children', []):
                    for raw_unit in grandchild['zoneTable'].values():
                        serial = raw_unit.get('serial', '')
                        address = raw_unit.get('address', '')
                        if serial and address:
                            addresses[serial] = address
        except (KeyError, IndexError, TypeError):
            pass
        if addresses:
            _LOGGER.info("Preserved %d cached addresses", len(addresses))
        return addresses

    def get_raw_json(self):
        """Return raw dict retrieved from KumoCloud"""
        return self._kumo_dict

    def get_all_units(self):
        """ Return list of all unit serial numbers
        """
        self._fetch_if_needed()

        return self._units.keys()

    def get_indoor_units(self):
        """ Return list of indoor unit serial numbers
        """

        return list(filter(
            lambda x: self.is_indoor_unit(x), self.get_all_units()))

    def get_kumo_stations(self):
        """ Return list of kumo station serial numbers
        """

        return list(filter(
            lambda x: self.is_headless_unit(x), self.get_all_units()))

    def get_name(self, unit):
        """ Return name of given unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['label']

        except KeyError:
            pass

        return None

    def is_indoor_unit(self, unit):
        """ Return whether unit is an indoor unit
        """
        self._fetch_if_needed()

        unit_type = self.get_unit_type(unit)
        if unit_type == "headless":
            return False
        return True

    def is_headless_unit(self, unit):
        """ Return whether unit is a headless unit (KumoStation)
        """
        self._fetch_if_needed()

        unit_type = self.get_unit_type(unit)
        if unit_type == "headless":
            return True
        return False

    def get_unit_type(self, unit):
        """ Return unit type of given unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['unitType']

        except KeyError:
            pass

        return None

    def get_address(self, unit):
        """ Return IP address of named unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['address']

        except KeyError:
            pass

        return None

    def get_mac(self, unit):
        """ Return mac address of named unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['mac']

        except KeyError:
            pass

        return None

    def get_credentials(self, unit):
        """ Return dict of credentials required to talk to unit
        """
        self._fetch_if_needed()

        try:
            credentials = {'password': self._units[unit]['password'],
                           'crypto_serial': self._units[unit]['cryptoSerial']}
            return credentials

        except KeyError:
            pass

        return None

    def make_pykumos(self, timeouts=None, init_update_status=True, use_schedule=False):
        """ Return a dict mapping names of all indoor units to newly-created
        `PyKumoBase` objects
        """
        kumos = {}
        for unitSerial in list(self.get_all_units()):
            name = self.get_name(unitSerial)
            if name in kumos:
                # I'm not sure if it's possible to have the same name repeated,
                # but just in case...
                m = re.match(r'(.*) \(([0-9]*)\)', name)
                if m:
                    name = m.group(1) + ' ({})'.format(int(m.group(2)) + 1)
                else:
                    kumos[name + ' (1)'] = kumos.pop(name)
                    name = name + ' (2)'
                # results in a name like "A/C unit (2)"
            unitType = self.get_unit_type(unitSerial)

            kumo_class = KUMO_UNIT_TYPE_TO_CLASS.get(unitType, PyKumo)
            kumos[name] = kumo_class(
                name=name,
                addr=self.get_address(unitSerial),
                cfg_json=self.get_credentials(unitSerial),
                timeouts=timeouts,
                serial=unitSerial,
                use_schedule=use_schedule,
            )

        if init_update_status:
            for pk in kumos.values():
                pk.update_status()

        return kumos
