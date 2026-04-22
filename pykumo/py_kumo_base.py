""" Class used to represent indoor units
"""

import hashlib
import base64
import time
import logging
import threading
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import Timeout
from .const import (CACHE_INTERVAL_SECONDS, W_PARAM, S_PARAM, UNIT_CONNECT_TIMEOUT_SECONDS,
                    UNIT_RESPONSE_TIMEOUT_SECONDS)

_LOGGER = logging.getLogger(__name__)

# One persistent requests.Session per thread, keyed by unit address.
# requests.Session reuses the underlying TCP connection (HTTP/1.1 keep-alive)
# across calls, eliminating the socket exhaustion on the Kumo WiFi adapter
# caused by opening a new connection on every poll (hass-kumo issue #82).
#
# threading.local is used instead of storing the session on self because
# hass-kumo dispatches pykumo calls via HA's shared ThreadPoolExecutor
# (async_add_executor_job), meaning the same PyKumo instance can land on
# different threads across successive calls. requests.Session is not
# thread-safe, so a per-thread store is required.
_tl = threading.local()


def _get_session(address: str, timeouts: tuple) -> requests.Session:
    """Return a persistent Session for (current_thread, address).

    Creates a new Session on first access per thread. The Session is
    configured with a single retry (backoff 0.1s) and keep-alive enabled
    via the default HTTPAdapter (pool_connections=1, pool_maxsize=1 is
    sufficient for a single-endpoint device).
    """
    if not hasattr(_tl, "sessions"):
        _tl.sessions = {}

    session = _tl.sessions.get(address)
    if session is None:
        _LOGGER.debug(
            "Creating new persistent Session for %s on thread %s",
            address, threading.current_thread().name
        )
        session = requests.Session()
        # Single retry with a short backoff — the adapter is local LAN,
        # so we don't want aggressive retries masking real failures.
        adapter = HTTPAdapter(
            max_retries=1,
            pool_connections=1,
            pool_maxsize=1,
        )
        session.mount("http://", adapter)
        _tl.sessions[address] = session

    return session


def _drop_session(address: str) -> None:
    """Close and discard the thread-local session for address.

    Called on transport-level errors so the next attempt gets a fresh
    connection rather than reusing a broken one.
    """
    sessions = getattr(_tl, "sessions", {})
    session = sessions.pop(address, None)
    if session is not None:
        try:
            session.close()
        except Exception:
            pass
        _LOGGER.debug(
            "Dropped Session for %s on thread %s",
            address, threading.current_thread().name
        )


class PyKumoBase:
    """ Talk to and control one indoor unit.
    """
    # pylint: disable=R0904, R0902

    def __init__(self, name, addr, cfg_json, timeouts=None, serial=None):
        """ Constructor
        """
        self._name = name
        self._address = addr
        self._serial = serial
        self._security = {
            'password': base64.b64decode(cfg_json["password"]),
            'crypto_serial': bytearray.fromhex(cfg_json["crypto_serial"])}
        if not timeouts:
            _LOGGER.info("Use default timeouts")
            self._timeouts = (UNIT_CONNECT_TIMEOUT_SECONDS,
                              UNIT_RESPONSE_TIMEOUT_SECONDS)
        else:
            _LOGGER.info("Use timeouts=%s", str(timeouts))
            connect_timeout = timeouts[0] if timeouts[0] else UNIT_CONNECT_TIMEOUT_SECONDS
            response_timeout = timeouts[1] if timeouts[1] else UNIT_RESPONSE_TIMEOUT_SECONDS
            self._timeouts = (connect_timeout, response_timeout)
        self._status = {}
        self._profile = {}
        self._sensors = []
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS

    def _token(self, post_data):
        """ Compute URL including security token for a given command
        """
        data_hash = hashlib.sha256(self._security['password'] +
                                   post_data).digest()

        intermediate = bytearray(88)
        intermediate[0:32] = W_PARAM[0:32]
        intermediate[32:64] = data_hash[0:32]
        intermediate[64:66] = bytearray.fromhex("0840")
        intermediate[66] = S_PARAM
        intermediate[79] = self._security['crypto_serial'][8]
        intermediate[80:84] = self._security['crypto_serial'][4:8]
        intermediate[84:88] = self._security['crypto_serial'][0:4]

        token = hashlib.sha256(intermediate).hexdigest()

        return token

    def _request(self, post_data):
        """ Send request to configured unit and return response dict

        Uses a thread-local persistent requests.Session instead of
        opening a new Session (and thus a new TCP connection) on every
        call. On transport error the session is discarded and one retry
        is attempted with a fresh connection.
        """
        if not self._address:
            _LOGGER.warning("Unit %s address not set", self._name)
            return {}
        url = "http://" + self._address + "/api"
        token = self._token(post_data)
        headers = {'Accept': 'application/json, text/plain, */*',
                   'Content-Type': 'application/json'}
        token_param = {'m': token}

        for attempt in range(2):
            session = _get_session(self._address, self._timeouts)
            try:
                _LOGGER.debug("Issue request %s %s (attempt %d)", url, post_data, attempt)
                response = session.put(
                    url,
                    headers=headers,
                    data=post_data,
                    params=token_param,
                    timeout=self._timeouts,
                )
                result = response.json()
                response.close()  # explicitly return socket to pool / graceful FIN
                return result

            except Timeout as ex:
                _LOGGER.warning("Timeout issuing request %s: %s", url, str(ex))
                _drop_session(self._address)
                return {}

            except Exception as ex:
                _LOGGER.debug(
                    "Request error on attempt %d for %s: %s (%s)",
                    attempt, url, str(ex), type(ex).__name__
                )
                _drop_session(self._address)
                if attempt == 1:
                    _LOGGER.warning("Error issuing request %s: %s", url, str(ex))
                    return {}

        return {}

    def get_status(self):
        """ Last retrieved status dictionary from unit """
        return self._status

    def update_status(self):
        """ Retrieve and cache current status dictionary if enough time
            has passed
        """
        raise NotImplementedError()

    def get_name(self):
        """ Unit's name """
        return self._name

    def get_serial(self):
        """ Unit's serial number """
        return self._serial

    def get_sensor_rssi(self):
        """ Last retrievd sensor signal strength, if any """
        val = None
        try:
            for sensor in self._sensors:
                if sensor['rssi'] is not None:
                    return sensor['rssi']
        except KeyError:
            val = None
        return val

    def get_wifi_rssi(self):
        """ Last retrieved WiFi signal strengh, if any """
        val = None
        try:
            val = self._profile['wifiRSSI']
        except KeyError:
            val = None
        return val
