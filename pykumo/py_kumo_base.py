""" Class used to represent indoor units
"""

import hashlib
import base64
import json
import time
import logging
import threading
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import Timeout
from .const import (CACHE_INTERVAL_SECONDS, W_PARAM, S_PARAM, UNIT_CONNECT_TIMEOUT_SECONDS,
                    UNIT_RESPONSE_TIMEOUT_SECONDS)

_LOGGER = logging.getLogger(__name__)

# threading.local is used instead of storing the session on self because
# hass-kumo dispatches pykumo calls via HA's shared ThreadPoolExecutor
# (async_add_executor_job), meaning the same PyKumo instance can land on
# different threads across successive calls. requests.Session is not
# thread-safe, so a per-thread store is required.
_tl = threading.local()


def _get_session(address: str) -> requests.Session:
    """Return a persistent Session for (current_thread, address).

    Creates a new Session on first access per thread. pool_connections=1
    and pool_maxsize=1 with pool_block=True ensure urllib3 never silently
    opens secondary connections under contention — this guarantees exactly
    one TCP connection per (thread, unit) at any given time.
    """
    if not hasattr(_tl, "sessions"):
        _tl.sessions = {}

    session = _tl.sessions.get(address)
    if session is None:
        _LOGGER.debug(
            "Opening Session for %s on thread %s",
            address, threading.current_thread().name
        )
        session = requests.Session()
        adapter = HTTPAdapter(
            max_retries=0,         # we handle retries ourselves
            pool_connections=1,
            pool_maxsize=1,
            pool_block=True,       # serialize rather than opening a second conn
        )
        session.mount("http://", adapter)
        _tl.sessions[address] = session

    return session


def _drop_session(address: str) -> None:
    """Close and discard the thread-local session for address.

    session.close() closes all pooled connections, sending a FIN on any
    that are still idle in the pool. This tells the adapter to free its
    socket-table entry immediately rather than waiting for idle timeout.
    """
    sessions = getattr(_tl, "sessions", {})
    session = sessions.pop(address, None)
    if session is not None:
        try:
            session.close()
        except Exception:
            pass
        _LOGGER.debug(
            "Closed Session for %s on thread %s",
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

        # Cycle context: when True, _request keeps the session open across
        # calls for keep-alive reuse. When False, each _request closes the
        # session immediately after the response. Multi-request operations
        # like update_status wrap themselves in a cycle via begin_cycle()
        # / end_cycle(). Single-shot operations (set_mode, etc.) leave
        # this False and get immediate FIN after each call.
        self._in_cycle = False

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

    @staticmethod
    def _cleanup_response(response) -> None:
        """Defensively close a response that may be in an indeterminate
        state. Silently swallows any exception — this runs in error
        handlers where we must not raise.
        """
        if response is None:
            return
        try:
            response.close()
        except Exception:
            pass

    def begin_cycle(self):
        """Mark the start of a multi-request cycle. Subsequent _request
        calls will reuse the same TCP connection (keep-alive) until
        end_cycle() is called. Safe to call multiple times; idempotent.
        """
        self._in_cycle = True

    def end_cycle(self):
        """Mark the end of a multi-request cycle and close the session.
        Sends a FIN to the adapter, freeing its socket-table entry.
        Safe to call multiple times; idempotent.
        """
        self._in_cycle = False
        _drop_session(self._address)

    def close(self):
        """Close any open HTTP session to this unit. Alias for end_cycle()
        that reads more naturally from callers that aren't managing a
        cycle explicitly.
        """
        self.end_cycle()

    def _request(self, post_data):
        """ Send request to configured unit and return response dict.

        Connection lifecycle:
        - Inside a cycle (begin_cycle() called): keep the session open
          for reuse by subsequent calls in the same cycle.
        - Outside a cycle: close the session immediately after the
          response, sending a clean FIN to the adapter.

        Hardening:
        - Body fully drained before JSON parsing (no abandoned sockets on malformed responses)
        - response.close() in every exit path
        - Session dropped on ANY transport error
        - One retry on transport error with a fresh session
        """
        if not self._address:
            _LOGGER.warning("Unit %s address not set", self._name)
            return {}

        url = "http://" + self._address + "/api"
        token = self._token(post_data)
        headers = {
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
        }
        token_param = {'m': token}

        for attempt in range(2):
            session = _get_session(self._address)
            response = None
            try:
                _LOGGER.debug("Issue request %s %s (attempt %d)", url, post_data, attempt)
                response = session.put(
                    url,
                    headers=headers,
                    data=post_data,
                    params=token_param,
                    timeout=self._timeouts,
                )

                # Drain body BEFORE parsing. If JSON parsing fails, the
                # body is already fully read so urllib3 can return the
                # connection to the pool cleanly rather than abandoning it.
                content = response.content
                response.close()
                response = None

                result = json.loads(content.decode('utf-8'))

                # Close the session if this is a single-shot call
                # (outside any multi-request cycle).
                if not self._in_cycle:
                    _drop_session(self._address)

                return result

            except Timeout as ex:
                _LOGGER.warning("Timeout issuing request %s: %s", url, str(ex))
                self._cleanup_response(response)
                # A timeout means the connection state is unknowable —
                # drop it rather than risk reusing a half-dead socket.
                _drop_session(self._address)
                return {}

            except (json.JSONDecodeError, ValueError) as ex:
                _LOGGER.warning(
                    "Malformed response from %s: %s", url, str(ex)
                )
                self._cleanup_response(response)
                _drop_session(self._address)
                return {}

            except Exception as ex:
                _LOGGER.debug(
                    "Request error on attempt %d for %s: %s (%s)",
                    attempt, url, str(ex), type(ex).__name__
                )
                self._cleanup_response(response)
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
