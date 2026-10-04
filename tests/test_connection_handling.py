"""Tests for how PyKumoBase manages connections to the adapter.

These run against a small local HTTP server standing in for the adapter,
which records how many TCP connections are open at once and how many
requests it received.
"""

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pykumo import PyKumo
from pykumo.const import UNIT_MIN_REQUEST_INTERVAL_SECONDS
from pykumo.py_kumo_base import _get_adapter_gate

_CFG = {
    "password": "dGVzdA==",  # base64("test")
    "crypto_serial": "0123456789ABCDEF01234567",
}

# Superset response good enough for every query update_status() issues.
_RESPONSE = {
    "r": {
        "indoorUnit": {
            "status": {
                "mode": "heat",
                "standby": False,
                "spHeat": 21,
                "spCool": 24,
                "roomTemp": 20,
                "fanSpeed": "auto",
                "vaneDir": "auto",
                "filterDirty": False,
                "defrost": False,
                "tempSource": "unset",
                "activeThermistor": "unset",
            },
            "profile": {
                "numberOfFanSpeeds": 5,
                "hasFanSpeedAuto": True,
                "hasVaneSwing": True,
                "hasModeDry": True,
                "hasModeHeat": True,
                "hasModeVent": True,
                "hasModeAuto": True,
                "hasVaneDir": True,
            },
        },
        "sensors": {"0": {}},
        "adapter": {
            "status": {
                "autoModePrevention": False,
                "userHasModeDry": True,
                "userHasModeHeat": True,
                "localNetwork": {},
                "runState": "normal",
            }
        },
        "mhk2": None,
    }
}


class _FakeAdapter:
    """Threaded HTTP server that tracks concurrent connections."""

    def __init__(self):
        self.lock = threading.Lock()
        self.open = 0
        self.max_open = 0
        self.requests = 0
        self.last_headers = None
        self.arrivals = []  # (monotonic time, request body) per request
        self.hang = False
        self.delay = 0.02
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                with adapter.lock:
                    adapter.open += 1
                    adapter.max_open = max(adapter.max_open, adapter.open)

            def finish(self):
                try:
                    super().finish()
                finally:
                    with adapter.lock:
                        adapter.open -= 1

            def log_message(self, *args):
                pass

            def do_PUT(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                with adapter.lock:
                    adapter.requests += 1
                    adapter.last_headers = self.headers
                    adapter.arrivals.append((time.monotonic(), body))
                if adapter.hang:
                    time.sleep(1.0)
                    return
                time.sleep(adapter.delay)
                body = json.dumps(_RESPONSE).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.address = "127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TestConnectionHandling(unittest.TestCase):
    """Connection behaviour towards a single adapter."""

    def setUp(self):
        self.adapter = _FakeAdapter()
        self.addCleanup(self.adapter.close)

    def _make_unit(self, timeouts=(1.0, 1.0), min_request_interval=0):
        return PyKumo(
            "Test Unit",
            self.adapter.address,
            _CFG,
            timeouts=timeouts,
            min_request_interval=min_request_interval,
        )

    def test_one_connection_at_a_time_across_threads(self):
        """Polls and commands from different threads never overlap on the wire."""
        unit = self._make_unit()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = []
            for _ in range(5):
                unit._last_status_update = 0  # force a real poll
                futures.append(pool.submit(unit.update_status))
                futures += [pool.submit(unit.set_fan_speed, "low") for _ in range(3)]
                futures.append(pool.submit(unit.set_mode, "cool"))
            for future in futures:
                future.result()
        self.assertGreater(self.adapter.requests, 20)
        self.assertEqual(self.adapter.max_open, 1)

    def test_unresponsive_adapter_fails_fast(self):
        """Once the adapter stops answering, the rest of the poll is skipped."""
        unit = self._make_unit(timeouts=(0.5, 0.2))
        self.adapter.hang = True
        self.assertFalse(unit.update_status())
        # One query plus its single retry, not a fan-out to every attribute.
        self.assertEqual(self.adapter.requests, 2)

    def test_next_poll_retries_after_failed_cycle(self):
        """A failed cycle doesn't poison later polls."""
        unit = self._make_unit(timeouts=(0.5, 0.2))
        self.adapter.hang = True
        self.assertFalse(unit.update_status())
        self.adapter.hang = False
        self.assertTrue(unit.update_status())
        self.assertEqual(unit.get_mode(), "heat")

    def test_single_shot_after_failed_cycle_is_sent(self):
        """Fail-fast only applies inside the cycle that failed."""
        unit = self._make_unit(timeouts=(0.5, 0.2))
        self.adapter.hang = True
        unit.update_status()
        self.adapter.hang = False
        before = self.adapter.requests
        self.assertTrue(unit.set_mode("cool"))
        self.assertEqual(self.adapter.requests, before + 1)

    def test_no_user_agent_header(self):
        """Requests carry no User-Agent but still advertise Accept-Encoding."""
        unit = self._make_unit()
        unit.set_mode("cool")
        self.assertNotIn("User-Agent", self.adapter.last_headers)
        self.assertIn("Accept-Encoding", self.adapter.last_headers)

    def test_requests_are_spaced(self):
        """Requests from any thread leave the adapter a minimum idle gap."""
        interval = 0.15
        unit = self._make_unit(min_request_interval=interval)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(unit.set_fan_speed, "low") for _ in range(4)]
            for future in futures:
                future.result()
        times = [t for t, _ in self.adapter.arrivals]
        self.assertEqual(len(times), 4)
        gaps = [b - a for a, b in zip(times, times[1:])]
        # Arrival-to-arrival includes the response time on top of the gap.
        for gap in gaps:
            self.assertGreaterEqual(gap, interval + self.adapter.delay - 0.01)

    def test_retry_is_spaced(self):
        """The retry after a timeout also waits for the interval."""
        interval = 0.3
        unit = self._make_unit(timeouts=(0.5, 0.2), min_request_interval=interval)
        self.adapter.hang = True
        unit.set_mode("cool")
        times = [t for t, _ in self.adapter.arrivals]
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], 0.2 + interval - 0.01)

    def test_waiting_requests_are_served_in_order(self):
        """Threads waiting on a busy adapter go first-come-first-served."""
        unit = self._make_unit()
        gate = _get_adapter_gate(self.adapter.address)
        speeds = ["quiet", "low", "powerful", "superPowerful", "superQuiet"]
        threads = []
        gate.acquire()  # adapter busy, e.g. a poll in progress
        try:
            for i, speed in enumerate(speeds):
                thread = threading.Thread(target=unit.set_fan_speed, args=(speed,))
                thread.start()
                threads.append(thread)
                deadline = time.monotonic() + 2.0
                while len(gate._queue) < i + 1 and time.monotonic() < deadline:
                    time.sleep(0.005)
        finally:
            gate.release()
        for thread in threads:
            thread.join()
        sent = [
            json.loads(body)["c"]["indoorUnit"]["status"]["fanSpeed"]
            for _, body in self.adapter.arrivals
        ]
        self.assertEqual(sent, speeds)

    def test_cycle_releases_adapter_gate(self):
        """begin_cycle() is idempotent and end_cycle() frees the adapter."""
        unit = self._make_unit()
        unit.begin_cycle()
        unit.begin_cycle()
        unit.end_cycle()
        unit.end_cycle()

        def other_thread():
            with _get_adapter_gate(self.adapter.address):
                pass

        thread = threading.Thread(target=other_thread, daemon=True)
        thread.start()
        thread.join(timeout=1.0)
        self.assertFalse(thread.is_alive())

    def test_default_interval_applies(self):
        """Units get the library default interval unless told otherwise."""
        unit = PyKumo("Test Unit", self.adapter.address, _CFG)
        self.assertEqual(unit._min_request_interval, UNIT_MIN_REQUEST_INTERVAL_SECONDS)


if __name__ == "__main__":
    unittest.main()
