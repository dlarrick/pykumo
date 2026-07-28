"""Tests for the CN105 bus transport and PyKumo's cached telemetry."""

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from pykumo.cn105 import CompressorActivityEstimator, InfoCode
from pykumo.cn105_bus import Cn105Bus
from pykumo.py_kumo import PyKumo
from tests.cn105_frames import (
    FRAME_03_HEX,
    FRAME_09_ACTIVE_HEX,
    INFO_03_REQUEST_HEX,
    make_0x06_reply,
)


def readback(frame: bytes | None) -> dict:
    """Build the response the adapter gives back when you read the buffer."""
    node = {"frame": frame.hex()} if frame else {}
    return {"r": {"indoorUnit": {"settings": {"rawITPFrame": node}}}}


class FakeUnit:
    """A fake PyKumo: just a name, a request hook, and cycle counting."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.requests = []
        # Requests made outside a cycle, meaning they had to reconnect.
        self.unbracketed = 0
        self.cycles_opened = 0
        self._depth = 0

    def get_name(self):
        return "Fake Unit"

    def _request(self, post_data):
        self.requests.append(post_data)
        if self._depth == 0:
            self.unbracketed += 1
        return self.responses.pop(0) if self.responses else {}

    @contextmanager
    def request_cycle(self):
        self.cycles_opened += 1
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1


def make_bus(responses=None):
    unit = FakeUnit(responses)
    return Cn105Bus(unit), unit


def make_unit(replies=None):
    """Build a PyKumo that reaches no network and answers with ``replies``."""
    with patch.object(PyKumo, "__init__", lambda self, *a, **kw: None):
        unit = PyKumo.__new__(PyKumo)
    unit._name = "Test Unit"
    unit._address = "192.0.2.1"
    unit._status = {}
    unit._profile = {}
    unit._cn105 = Cn105Bus(unit)
    unit._cn105_telemetry = {}
    unit._cn105_telemetry_at = None
    unit._compressor_activity = CompressorActivityEstimator()
    unit.sent_codes = []

    replies = replies or {}

    def fake_send(frame, id_byte=1):
        unit.sent_codes.append(frame[5])
        return True

    def fake_read():
        return replies.get(unit.sent_codes[-1]) if unit.sent_codes else None

    unit._cn105.send = fake_send
    unit._cn105.read = fake_read
    return unit


class TestSend(unittest.TestCase):
    def test_command_shape(self):
        bus, unit = make_bus([{"r": {}}])
        frame = bytes.fromhex(INFO_03_REQUEST_HEX)
        self.assertTrue(bus.send(frame))
        expected = (
            '{"c":{"indoorUnit":{"settings":{"rawITPFrame":'
            '{"frame":"%s","len":%d,"id":1}}}}}' % (frame.hex(), len(frame))
        ).encode("utf-8")
        self.assertEqual(unit.requests, [expected])

    def test_custom_id_byte(self):
        bus, unit = make_bus([{"r": {}}])
        bus.send(bytes.fromhex(INFO_03_REQUEST_HEX), id_byte=2)
        self.assertIn(b'"id":2', unit.requests[0])

    def test_empty_frame_is_rejected_without_a_request(self):
        bus, unit = make_bus()
        self.assertFalse(bus.send(b""))
        self.assertEqual(unit.requests, [])

    def test_overlong_frame_is_rejected_without_a_request(self):
        bus, unit = make_bus()
        self.assertFalse(bus.send(b"\x00" * 128))
        self.assertEqual(unit.requests, [])

    def test_out_of_range_id_byte_is_rejected_without_a_request(self):
        bus, unit = make_bus()
        self.assertFalse(bus.send(bytes.fromhex(INFO_03_REQUEST_HEX), id_byte=256))
        self.assertEqual(unit.requests, [])

    def test_api_error_is_a_failure(self):
        bus, _ = make_bus([{"_api_error": "device_authentication_error"}])
        self.assertFalse(bus.send(bytes.fromhex(INFO_03_REQUEST_HEX)))

    def test_empty_response_is_a_failure(self):
        bus, _ = make_bus([None])
        self.assertFalse(bus.send(bytes.fromhex(INFO_03_REQUEST_HEX)))


class TestRead(unittest.TestCase):
    def test_reads_back_a_frame(self):
        reply = bytes.fromhex(FRAME_03_HEX)
        bus, _ = make_bus([readback(reply)])
        self.assertEqual(bus.read(), reply)

    def test_empty_buffer(self):
        bus, _ = make_bus([readback(None)])
        self.assertIsNone(bus.read())

    def test_malformed_response(self):
        bus, _ = make_bus([{"r": {}}])
        self.assertIsNone(bus.read())

    def test_none_response(self):
        bus, _ = make_bus([None])
        self.assertIsNone(bus.read())

    def test_non_hex_payload(self):
        bus, _ = make_bus(
            [{"r": {"indoorUnit": {"settings": {"rawITPFrame": {"frame": "zz"}}}}}]
        )
        self.assertIsNone(bus.read())


class TestTransceive(unittest.TestCase):
    def _bus(self, reads):
        """A bus whose send works and whose reads hand back ``reads`` in order."""
        bus, unit = make_bus([{"r": {}}] + [readback(r) for r in reads])
        return bus, unit

    def test_returns_the_matching_reply(self):
        reply = bytes.fromhex(FRAME_03_HEX)
        bus, _ = self._bus([reply])
        with patch("pykumo.cn105_bus.time.sleep"):
            got = bus.transceive(
                bytes.fromhex(INFO_03_REQUEST_HEX), expect_type=0x62, expect_code=0x03
            )
        self.assertEqual(got, reply)

    def test_skips_a_stale_reply_for_another_code(self):
        stale = bytes.fromhex(FRAME_09_ACTIVE_HEX)
        wanted = bytes.fromhex(FRAME_03_HEX)
        bus, _ = self._bus([stale, None, wanted])
        with patch("pykumo.cn105_bus.time.sleep"):
            got = bus.transceive(
                bytes.fromhex(INFO_03_REQUEST_HEX), expect_type=0x62, expect_code=0x03
            )
        self.assertEqual(got, wanted)

    def test_timeout_returns_none(self):
        bus, _ = self._bus([None, None])
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertIsNone(
                bus.transceive(bytes.fromhex(INFO_03_REQUEST_HEX), timeout=1.0)
            )

    def test_failed_send_skips_polling(self):
        bus, unit = make_bus([{"_api_error": "x"}])
        with patch("pykumo.cn105_bus.time.sleep") as sleep:
            self.assertIsNone(bus.transceive(bytes.fromhex(INFO_03_REQUEST_HEX)))
        sleep.assert_not_called()
        self.assertEqual(len(unit.requests), 1)

    def test_every_request_reuses_one_connection(self):
        # Without a cycle, _request closes the session after every call, so a
        # 20 s wait would mean dozens of reconnects.
        bus, unit = self._bus([None, None, bytes.fromhex(FRAME_03_HEX)])
        with patch("pykumo.cn105_bus.time.sleep"):
            bus.transceive(
                bytes.fromhex(INFO_03_REQUEST_HEX), expect_type=0x62, expect_code=0x03
            )
        self.assertEqual(unit.unbracketed, 0)
        self.assertEqual(unit.cycles_opened, 1)
        self.assertEqual(len(unit.requests), 4)

    def test_poll_count_follows_the_timeout(self):
        bus, _ = self._bus([None] * 10)
        with patch("pykumo.cn105_bus.time.sleep") as sleep:
            bus.transceive(bytes.fromhex(INFO_03_REQUEST_HEX), timeout=2.0)
        self.assertEqual(sleep.call_count, 4)


class TestUnsupportedCodeLatch(unittest.TestCase):
    def _bus_that_never_answers(self):
        bus, unit = make_bus()
        unit.responses = [{"r": {}}] * 200
        return bus, unit

    def test_compressor_code_latches_after_one_miss(self):
        bus, unit = self._bus_that_never_answers()
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertIsNone(bus.read_info(InfoCode.COMPRESSOR, timeout=1.0))
        self.assertEqual(bus.unsupported_codes, frozenset({InfoCode.COMPRESSOR}))
        before = len(unit.requests)
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertIsNone(bus.read_info(InfoCode.COMPRESSOR, timeout=1.0))
        # Given up on, so the second call must not touch the adapter at all.
        self.assertEqual(len(unit.requests), before)

    def test_default_codes_never_latch(self):
        # One miss on a code every unit supports must not disable it.
        bus, _ = self._bus_that_never_answers()
        with patch("pykumo.cn105_bus.time.sleep"):
            bus.read_info(InfoCode.TEMPERATURES, timeout=1.0)
            bus.read_info(InfoCode.SUB_MODE, timeout=1.0)
        self.assertEqual(bus.unsupported_codes, frozenset())

    def test_a_code_that_answered_once_never_latches(self):
        reply = make_0x06_reply(operating_byte=0x01)
        bus, unit = make_bus([{"r": {}}, readback(reply)])
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertEqual(bus.read_info(InfoCode.COMPRESSOR, timeout=1.0), reply)
        unit.responses = [{"r": {}}] * 200
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertIsNone(bus.read_info(InfoCode.COMPRESSOR, timeout=1.0))
        self.assertEqual(bus.unsupported_codes, frozenset())

    def test_forget_unsupported_allows_a_retry(self):
        bus, unit = self._bus_that_never_answers()
        with patch("pykumo.cn105_bus.time.sleep"):
            bus.read_info(InfoCode.COMPRESSOR, timeout=1.0)
        bus.forget_unsupported()
        self.assertEqual(bus.unsupported_codes, frozenset())
        before = len(unit.requests)
        with patch("pykumo.cn105_bus.time.sleep"):
            bus.read_info(InfoCode.COMPRESSOR, timeout=1.0)
        self.assertGreater(len(unit.requests), before)


class TestUpdateCn105Telemetry(unittest.TestCase):
    def test_default_codes_are_temperatures_and_sub_mode(self):
        unit = make_unit(
            {
                0x03: bytes.fromhex(FRAME_03_HEX),
                0x09: bytes.fromhex(FRAME_09_ACTIVE_HEX),
            }
        )
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertTrue(unit.update_cn105_telemetry())
        self.assertEqual(unit.sent_codes, [0x03, 0x09])
        self.assertEqual(
            unit.get_cn105_telemetry(),
            {
                "room_temperature": 22.0,
                "outdoor_temperature": 27.0,
                "compressor_runtime_minutes": 137918,
                "sub_mode": "NORMAL",
                "stage": "GENTLE",
                "auto_sub_mode": "AUTO_INACTIVE",
                "operating": None,
            },
        )

    def test_keys_present_and_false_when_nothing_answers(self):
        unit = make_unit({})
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertFalse(unit.update_cn105_telemetry())
        self.assertEqual(
            sorted(unit.get_cn105_telemetry()),
            [
                "auto_sub_mode",
                "compressor_runtime_minutes",
                "operating",
                "outdoor_temperature",
                "room_temperature",
                "stage",
                "sub_mode",
            ],
        )
        self.assertTrue(all(v is None for v in unit.get_cn105_telemetry().values()))

    def test_compressor_code_is_opt_in(self):
        unit = make_unit({0x06: make_0x06_reply(freq_byte=0x2A, operating_byte=0x01)})
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.COMPRESSOR])
        self.assertEqual(unit.sent_codes, [0x06])
        self.assertEqual(
            unit.get_cn105_telemetry(),
            {"operating": True, "compressor_frequency": 0x2A},
        )

    def test_one_code_failing_keeps_the_other(self):
        unit = make_unit({0x09: bytes.fromhex(FRAME_09_ACTIVE_HEX)})
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertTrue(unit.update_cn105_telemetry())
        telemetry = unit.get_cn105_telemetry()
        self.assertEqual(telemetry["sub_mode"], "NORMAL")
        self.assertIsNone(telemetry["outdoor_temperature"])

    def test_unknown_code_is_skipped_without_a_send(self):
        unit = make_unit({})
        with patch("pykumo.cn105_bus.time.sleep"):
            self.assertFalse(unit.update_cn105_telemetry(codes=[0x11]))
        self.assertEqual(unit.sent_codes, [])
        self.assertEqual(unit.get_cn105_telemetry(), {})

    def test_a_raising_read_is_contained(self):
        unit = make_unit({})
        unit._cn105.read_info = lambda code, timeout=None: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        self.assertFalse(unit.update_cn105_telemetry())
        self.assertTrue(all(v is None for v in unit.get_cn105_telemetry().values()))

    def test_a_refresh_replaces_the_previous_snapshot(self):
        unit = make_unit({0x03: bytes.fromhex(FRAME_03_HEX)})
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])
        self.assertEqual(unit.get_outdoor_temperature(), 27.0)
        # The unit stops answering, so the old values must not stick around.
        unit._cn105.read = lambda: None
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])
        self.assertIsNone(unit.get_outdoor_temperature())


class TestCachedAccessors(unittest.TestCase):
    def test_none_before_any_refresh(self):
        unit = make_unit()
        self.assertIsNone(unit.get_outdoor_temperature())
        self.assertIsNone(unit.get_raw_room_temperature())
        self.assertIsNone(unit.get_compressor_runtime_minutes())
        self.assertEqual(unit.get_cn105_telemetry(), {})
        self.assertIsNone(unit.get_cn105_telemetry_age())

    def test_values_after_a_refresh(self):
        unit = make_unit({0x03: bytes.fromhex(FRAME_03_HEX)})
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])
        self.assertEqual(unit.get_outdoor_temperature(), 27.0)
        self.assertEqual(unit.get_raw_room_temperature(), 22.0)
        self.assertEqual(unit.get_compressor_runtime_minutes(), 137918)

    def test_accessors_do_not_touch_the_adapter(self):
        unit = make_unit({0x03: bytes.fromhex(FRAME_03_HEX)})
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])
        unit.sent_codes.clear()
        unit.get_outdoor_temperature()
        unit.get_raw_room_temperature()
        unit.get_compressor_runtime_minutes()
        unit.get_cn105_telemetry()
        self.assertEqual(unit.sent_codes, [])

    def test_telemetry_snapshot_is_a_copy(self):
        unit = make_unit({0x03: bytes.fromhex(FRAME_03_HEX)})
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])
        unit.get_cn105_telemetry()["outdoor_temperature"] = 999
        self.assertEqual(unit.get_outdoor_temperature(), 27.0)

    def test_age_tracks_the_last_answer(self):
        unit = make_unit({0x03: bytes.fromhex(FRAME_03_HEX)})
        with (
            patch("pykumo.cn105_bus.time.sleep"),
            patch(
                "pykumo.py_kumo.time.monotonic", side_effect=[1000.0, 1000.0, 1042.0]
            ),
        ):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])
            self.assertEqual(unit.get_cn105_telemetry_age(), 42.0)

    def test_age_stays_none_when_nothing_answers(self):
        unit = make_unit({})
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])
        self.assertIsNone(unit.get_cn105_telemetry_age())


class TestIsCompressorRunning(unittest.TestCase):
    def _refresh(self, unit, now):
        with (
            patch("pykumo.cn105_bus.time.sleep"),
            patch("pykumo.py_kumo.time.monotonic", return_value=now),
        ):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])

    def test_operating_flag_wins_when_the_compressor_code_answered(self):
        unit = make_unit({0x06: make_0x06_reply(operating_byte=0x01)})
        unit._status = {"mode": "cool"}
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.COMPRESSOR])
        self.assertIs(unit.is_compressor_running(), True)

    def test_operating_flag_false_is_respected(self):
        unit = make_unit({0x06: make_0x06_reply(operating_byte=0x00)})
        unit._status = {"mode": "cool"}
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.COMPRESSOR])
        self.assertIs(unit.is_compressor_running(), False)

    def test_off_mode_is_false_without_the_compressor_code(self):
        unit = make_unit({0x03: bytes.fromhex(FRAME_03_HEX)})
        unit._status = {"mode": "off"}
        self.assertIs(unit.is_compressor_running(), False)

    def test_undetermined_until_two_samples_exist(self):
        unit = make_unit({0x03: bytes.fromhex(FRAME_03_HEX)})
        unit._status = {"mode": "cool"}
        self._refresh(unit, 1000.0)
        self.assertIsNone(unit.is_compressor_running())

    def test_falls_back_to_the_runtime_estimate(self):
        replies = {0x03: bytes.fromhex(FRAME_03_HEX)}
        unit = make_unit(replies)
        unit._status = {"mode": "cool"}
        self._refresh(unit, 1000.0)
        # Same counter 80 s later, so the compressor did not run.
        self._refresh(unit, 1080.0)
        self.assertIs(unit.is_compressor_running(), False)
        # A frame whose counter advanced by one minute.
        advanced = bytearray(bytes.fromhex(FRAME_03_HEX))
        advanced[18] += 1
        advanced[-1] = (advanced[-1] - 1) & 0xFF
        replies[0x03] = bytes(advanced)
        self._refresh(unit, 1090.0)
        self.assertIs(unit.is_compressor_running(), True)

    def test_a_slow_code_does_not_age_a_fresh_counter_reading(self):
        unit = make_unit({0x03: bytes.fromhex(FRAME_03_HEX)})
        unit._status = {"mode": "cool"}
        with (
            patch("pykumo.cn105_bus.time.sleep"),
            patch("pykumo.py_kumo.time.monotonic", side_effect=[1000.0, 1000.0]),
        ):
            unit.update_cn105_telemetry(codes=[InfoCode.TEMPERATURES])
        # 0x03 is read first, then 0x06 times out and burns 70 s. The counter
        # reading is only 10 s newer than the last one, so there is still
        # nothing to compare.
        with (
            patch("pykumo.cn105_bus.time.sleep"),
            patch("pykumo.py_kumo.time.monotonic", side_effect=[1010.0, 1080.0]),
        ):
            unit.update_cn105_telemetry(
                codes=[InfoCode.TEMPERATURES, InfoCode.COMPRESSOR]
            )
        self.assertIsNone(unit.is_compressor_running())

    def test_a_refresh_without_the_counter_does_not_resample_it(self):
        replies = {
            0x03: bytes.fromhex(FRAME_03_HEX),
            0x09: bytes.fromhex(FRAME_09_ACTIVE_HEX),
        }
        unit = make_unit(replies)
        unit._status = {"mode": "cool"}
        self._refresh(unit, 1000.0)
        # No 0x03 this time, so the cached counter must not stand in for a
        # second reading.
        with (
            patch("pykumo.cn105_bus.time.sleep"),
            patch("pykumo.py_kumo.time.monotonic", return_value=1200.0),
        ):
            unit.update_cn105_telemetry(codes=[InfoCode.SUB_MODE])
        self.assertIsNone(unit.is_compressor_running())

    def test_telemetry_operating_comes_from_the_estimate(self):
        replies = {0x03: bytes.fromhex(FRAME_03_HEX)}
        unit = make_unit(replies)
        unit._status = {"mode": "cool"}
        self._refresh(unit, 1000.0)
        self._refresh(unit, 1080.0)
        # 0x06 was never asked for, so the snapshot carries the estimate.
        self.assertIs(unit.get_cn105_telemetry()["operating"], False)

    def test_telemetry_operating_uses_the_flag_when_present(self):
        unit = make_unit({0x06: make_0x06_reply(operating_byte=0x01)})
        unit._status = {"mode": "cool"}
        with patch("pykumo.cn105_bus.time.sleep"):
            unit.update_cn105_telemetry(codes=[InfoCode.COMPRESSOR])
        self.assertIs(unit.get_cn105_telemetry()["operating"], True)


if __name__ == "__main__":
    unittest.main()
