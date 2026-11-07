"""Class used to represent indoor units"""

import collections
import hashlib
import base64
import json
import time
import logging
import threading
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout
from urllib3.util import SKIP_HEADER
from .const import (
    CACHE_INTERVAL_SECONDS,
    W_PARAM,
    S_PARAM,
    UNIT_CONNECT_TIMEOUT_SECONDS,
    UNIT_MIN_REQUEST_INTERVAL_SECONDS,
    UNIT_RESPONSE_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# threading.local is used instead of storing the session on self because
# hass-kumo dispatches pykumo calls via HA's shared ThreadPoolExecutor
# (async_add_executor_job), meaning the same PyKumo instance can land on
# different threads across successive calls. requests.Session is not
# thread-safe, so a per-thread store is required.
_tl = threading.local()


# Process-wide, per-adapter gates. Thread-local sessions alone do not bound
# the number of connections to an adapter: HA's executor can run a poll and
# one or more commands (or two polls) for the same unit on different threads
# at once, each with its own session and socket. The adapters have very few
# socket slots and little memory, so all traffic to a given address goes
# through one gate, which:
# - serializes it: at most one in-flight request (and one TCP connection)
#   per adapter per process;
# - queues waiting threads first-come-first-served, so a command issued
#   during a poll runs right after it rather than at an arbitrary point;
# - spaces requests out so the adapter gets a minimum idle gap between the
#   end of one request and the start of the next.
# The gate is reentrant so that requests nested inside a cycle on the same
# thread (e.g. do_reboot() or schedule fetch() during update_status()) don't
# deadlock.
class _AdapterGate:
    """FIFO, reentrant, rate-limited gate for one adapter address."""

    def __init__(self, address: str):
        self._address = address
        self._cond = threading.Condition(threading.Lock())
        self._queue = collections.deque()
        self._owner = None
        self._depth = 0
        # monotonic time the last request to this adapter finished; only
        # read or written by the thread that owns the gate.
        self._last_request_end = None

    def acquire(self):
        """Wait for this thread's turn, then own the gate."""
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
                return
            ticket = object()
            self._queue.append(ticket)
            if self._owner is not None:
                _LOGGER.debug(
                    "Queued request to %s behind %d other(s)",
                    self._address,
                    len(self._queue),
                )
            while self._owner is not None or self._queue[0] is not ticket:
                self._cond.wait()
            self._queue.popleft()
            self._owner = me
            self._depth = 1

    def release(self):
        """Give up one level of ownership; wake the next in line at zero."""
        with self._cond:
            if self._owner != threading.get_ident():
                raise RuntimeError("Releasing adapter gate not owned by thread")
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._cond.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

    def wait_for_slot(self, min_interval: float) -> None:
        """Sleep until min_interval has passed since the last request to
        this adapter finished. Caller must own the gate.
        """
        if self._last_request_end is None or min_interval <= 0:
            return
        delay = self._last_request_end + min_interval - time.monotonic()
        if delay > 0:
            _LOGGER.debug("Rate limit: waiting %.3fs for %s", delay, self._address)
            time.sleep(delay)

    def request_finished(self) -> None:
        """Record that a request just finished. Caller must own the gate."""
        self._last_request_end = time.monotonic()


_gates: dict[str, _AdapterGate] = {}
_gates_guard = threading.Lock()


def _get_adapter_gate(address: str) -> _AdapterGate:
    """Return the process-wide gate for traffic to address."""
    with _gates_guard:
        gate = _gates.get(address)
        if gate is None:
            gate = _AdapterGate(address)
            _gates[address] = gate
        return gate


def _get_session(address: str) -> requests.Session:
    """Return a persistent Session for (current_thread, address).

    Creates a new Session on first access per thread. pool_connections=1
    and pool_maxsize=1 with pool_block=True ensure urllib3 never silently
    opens secondary connections under contention. Across threads, the
    per-address gate (_get_adapter_gate) is what limits the adapter to one
    connection at a time; callers must own it while using the session.
    """
    if not hasattr(_tl, "sessions"):
        _tl.sessions = {}

    session = _tl.sessions.get(address)
    if session is None:
        _LOGGER.debug(
            "Opening Session for %s on thread %s",
            address,
            threading.current_thread().name,
        )
        session = requests.Session()
        adapter = HTTPAdapter(
            max_retries=0,  # we handle retries ourselves
            pool_connections=1,
            pool_maxsize=1,
            pool_block=True,  # serialize rather than opening a second conn
        )
        session.mount("http://", adapter)
        # Don't send a User-Agent: the adapter doesn't need one and it only
        # adds bytes to every request. Setting it to None isn't enough, as
        # urllib3 then adds its own default; SKIP_HEADER suppresses both.
        session.headers["User-Agent"] = SKIP_HEADER
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
            address,
            threading.current_thread().name,
        )


class PyKumoBase:
    """Talk to and control one indoor unit."""

    # pylint: disable=R0904, R0902

    def __init__(
        self,
        name,
        addr,
        cfg_json,
        timeouts=None,
        serial=None,
        min_request_interval=None,
    ):
        """Constructor

        min_request_interval: minimum seconds between the end of one request
        to this unit's adapter and the start of the next (default
        UNIT_MIN_REQUEST_INTERVAL_SECONDS). 0 disables rate limiting.
        """
        self._name = name
        self._address = addr
        self._serial = serial
        self._security = {
            "password": base64.b64decode(cfg_json["password"]),
            "crypto_serial": bytearray.fromhex(cfg_json["crypto_serial"]),
        }
        if not timeouts:
            _LOGGER.info("Use default timeouts")
            self._timeouts = (
                UNIT_CONNECT_TIMEOUT_SECONDS,
                UNIT_RESPONSE_TIMEOUT_SECONDS,
            )
        else:
            _LOGGER.info("Use timeouts=%s", str(timeouts))
            connect_timeout = (
                timeouts[0] if timeouts[0] else UNIT_CONNECT_TIMEOUT_SECONDS
            )
            response_timeout = (
                timeouts[1] if timeouts[1] else UNIT_RESPONSE_TIMEOUT_SECONDS
            )
            self._timeouts = (connect_timeout, response_timeout)
        if min_request_interval is None:
            min_request_interval = UNIT_MIN_REQUEST_INTERVAL_SECONDS
        self._min_request_interval = max(0.0, float(min_request_interval))
        self._status = {}
        self._profile = {}
        self._sensors = []
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS

    def _token(self, post_data):
        """Compute URL including security token for a given command"""
        data_hash = hashlib.sha256(self._security["password"] + post_data).digest()

        intermediate = bytearray(88)
        intermediate[0:32] = W_PARAM[0:32]
        intermediate[32:64] = data_hash[0:32]
        intermediate[64:66] = bytearray.fromhex("0840")
        intermediate[66] = S_PARAM
        intermediate[79] = self._security["crypto_serial"][8]
        intermediate[80:84] = self._security["crypto_serial"][4:8]
        intermediate[84:88] = self._security["crypto_serial"][0:4]

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

        The cycle holds the adapter's gate until end_cycle(), so commands
        issued from other threads wait for the cycle to finish instead of
        opening a second connection to the adapter. begin_cycle() and
        end_cycle() must be called from the same thread.

        Cycle state is stored thread-locally so concurrent threads calling
        into the same PyKumoBase instance each manage their own lifecycle
        independently.
        """
        if not hasattr(_tl, "cycles"):
            _tl.cycles = set()
        if self._address in _tl.cycles:
            return
        _get_adapter_gate(self._address).acquire()
        _tl.cycles.add(self._address)
        getattr(_tl, "failed_cycles", set()).discard(self._address)

    def end_cycle(self):
        """Mark the end of a multi-request cycle and close the session.
        Sends a FIN to the adapter, freeing its socket-table entry.
        Safe to call multiple times; idempotent.
        """
        _drop_session(self._address)
        getattr(_tl, "failed_cycles", set()).discard(self._address)
        cycles = getattr(_tl, "cycles", set())
        if self._address in cycles:
            cycles.discard(self._address)
            _get_adapter_gate(self._address).release()

    def close(self):
        """Close any open HTTP session to this unit. Alias for end_cycle()
        that reads more naturally from callers that aren't managing a
        cycle explicitly.
        """
        self.end_cycle()

    def _request(self, post_data):
        """Send request to configured unit and return response dict.

        Connection lifecycle:
        - Inside a cycle (begin_cycle() called): keep the session open
          for reuse by subsequent calls in the same cycle.
        - Outside a cycle: close the session immediately after the
          response, sending a clean FIN to the adapter.

        Hardening:
        - At most one request in flight per adapter across all threads;
          waiting threads are served first-come-first-served
        - At least min_request_interval seconds of idle time on the adapter
          between requests (including retries)
        - Body fully drained before JSON parsing (no abandoned sockets on malformed responses)
        - response.close() in every exit path
        - Session dropped on ANY transport error
        - One retry on transport error with a fresh session
        - Inside a cycle, once the adapter has failed to answer (timeout or
          connection error on both attempts), the rest of the cycle's
          requests fail fast instead of piling more connections onto an
          adapter that is already struggling
        """
        if not self._address:
            _LOGGER.warning("Unit %s address not set", self._name)
            return {}

        with _get_adapter_gate(self._address):
            return self._request_locked(post_data)

    def _request_locked(self, post_data):
        """Body of _request(); caller must own the adapter's gate."""
        gate = _get_adapter_gate(self._address)
        in_cycle = self._address in getattr(_tl, "cycles", set())
        if in_cycle and self._address in getattr(_tl, "failed_cycles", set()):
            _LOGGER.debug(
                "Skipping request to %s: adapter already failed this cycle",
                self._address,
            )
            return {}

        url = "http://" + self._address + "/api"
        token = self._token(post_data)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }
        token_param = {"m": token}

        for attempt in range(2):
            gate.wait_for_slot(self._min_request_interval)
            session = _get_session(self._address)
            response = None
            try:
                _LOGGER.debug(
                    "Issue request %s %s (attempt %d)", url, post_data, attempt
                )
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

                result = json.loads(content.decode("utf-8"))

                # Close the session if this is a single-shot call
                # (outside any multi-request cycle).
                if not in_cycle:
                    _drop_session(self._address)

                return result

            except Timeout as ex:
                _LOGGER.debug("Timeout on attempt %d for %s: %s", attempt, url, str(ex))
                self._cleanup_response(response)
                # A timeout means the connection state is unknowable —
                # drop it rather than risk reusing a half-dead socket.
                _drop_session(self._address)
                if attempt == 1:
                    _LOGGER.warning("Timeout issuing request %s: %s", url, str(ex))
                    self._mark_cycle_failed(in_cycle)
                    return {}
                # attempt == 0: fall through to retry with a fresh session

            except (json.JSONDecodeError, ValueError) as ex:
                _LOGGER.warning("Malformed response from %s: %s", url, str(ex))
                self._cleanup_response(response)
                _drop_session(self._address)
                return {}

            except Exception as ex:
                _LOGGER.debug(
                    "Request error on attempt %d for %s: %s (%s)",
                    attempt,
                    url,
                    str(ex),
                    type(ex).__name__,
                )
                self._cleanup_response(response)
                _drop_session(self._address)
                if attempt == 1:
                    _LOGGER.warning("Error issuing request %s: %s", url, str(ex))
                    if isinstance(ex, RequestsConnectionError):
                        self._mark_cycle_failed(in_cycle)
                    return {}

            finally:
                # Successful or not, the adapter just handled a request;
                # the next one waits min_request_interval from now.
                gate.request_finished()

        return {}

    def _mark_cycle_failed(self, in_cycle):
        """Record that the adapter stopped answering during this cycle."""
        if not in_cycle:
            return
        if not hasattr(_tl, "failed_cycles"):
            _tl.failed_cycles = set()
        _tl.failed_cycles.add(self._address)

    def has_profile(self) -> bool:
        """Return True if the unit profile has been populated from a successful poll.

        The profile starts as an empty dict at construction and is populated after
        the first successful ``update_status()`` call. Consumers (e.g. hass-kumo)
        should call this before relying on capability methods such as
        ``has_auto_mode()``, ``has_heat_mode()``, or ``get_fan_speeds()``:
        those methods silently return defaults (``False`` / a fallback list) when
        the profile is empty, which can cause incorrect behaviour if cached at
        initialisation time while the adapter is temporarily offline.
        """
        return bool(self._profile)

    def get_status(self):
        """Last retrieved status dictionary from unit"""
        return self._status

    def update_status(self):
        """Retrieve and cache current status dictionary if enough time
        has passed
        """
        raise NotImplementedError()

    def get_name(self):
        """Unit's name"""
        return self._name

    def get_serial(self):
        """Unit's serial number"""
        return self._serial

    def get_sensor_rssi(self):
        """Last retrieved sensor signal strength, if any"""
        val = None
        try:
            for sensor in self._sensors:
                if sensor["rssi"] is not None:
                    return sensor["rssi"]
        except KeyError:
            val = None
        return val

    def get_wifi_rssi(self):
        """Last retrieved WiFi signal strength, if any"""
        val = None
        try:
            val = self._profile["wifiRSSI"]
        except KeyError:
            val = None
        return val
