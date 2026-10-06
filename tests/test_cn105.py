"""Tests for CN105/ITP frame building, decoding, and the compressor estimator."""

import unittest

from pykumo.cn105 import (
    AUTO_SUB_MODE_NAMES,
    DEFAULT_INFO_CODES,
    PAYLOAD_SIZE,
    TELEMETRY_KEYS,
    CompressorActivityEstimator,
    InfoCode,
    build_cn105_frame,
    build_info_request,
    cn105_checksum,
    decode_info_reply,
    is_info_reply,
    valid_cn105_reply,
)
from tests.cn105_frames import (
    FRAME_03_ALT_HEX,
    FRAME_03_HEX,
    FRAME_09_ACTIVE_HEX,
    FRAME_09_OFF_HEX,
    INFO_03_REQUEST_HEX,
    INFO_06_REQUEST_HEX,
    INFO_09_REQUEST_HEX,
    make_0x03_reply,
    make_0x06_reply,
    make_0x09_reply,
    make_short_reply,
)


class TestChecksum(unittest.TestCase):
    def test_checksum_formula(self):
        self.assertEqual(cn105_checksum(bytes([0xFC, 0x42])), (0xFC - 0x13E) & 0xFF)

    def test_checksum_of_known_frame(self):
        frame = bytes.fromhex(INFO_03_REQUEST_HEX)
        self.assertEqual(cn105_checksum(frame[:-1]), frame[-1])


class TestFrameBuilding(unittest.TestCase):
    def test_info_requests_match_known_frames(self):
        self.assertEqual(build_info_request(0x03).hex(), INFO_03_REQUEST_HEX)
        self.assertEqual(build_info_request(0x06).hex(), INFO_06_REQUEST_HEX)
        self.assertEqual(build_info_request(0x09).hex(), INFO_09_REQUEST_HEX)

    def test_info_code_enum_builds_the_same_frame(self):
        self.assertEqual(
            build_info_request(InfoCode.TEMPERATURES), build_info_request(0x03)
        )

    def test_payload_is_padded_to_16(self):
        frame = build_cn105_frame(0x42, b"\x03")
        self.assertEqual(frame[4], PAYLOAD_SIZE)
        self.assertEqual(len(frame), PAYLOAD_SIZE + 6)

    def test_pad_to_zero_keeps_short_payload(self):
        frame = build_cn105_frame(0x5A, b"\xca", pad_to=0)
        self.assertEqual(frame[4], 1)
        self.assertEqual(len(frame), 7)

    def test_checksum_is_appended(self):
        frame = build_cn105_frame(0x42, b"\x03")
        self.assertEqual(frame[-1], cn105_checksum(frame[:-1]))

    def test_pad_to_beyond_payload_size_is_rejected(self):
        with self.assertRaises(ValueError):
            build_cn105_frame(0x42, b"\x03", pad_to=32)

    def test_oversized_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            build_cn105_frame(0x42, b"\x00" * 17)

    def test_out_of_range_type_byte_is_rejected(self):
        with self.assertRaises(ValueError):
            build_cn105_frame(0x100, b"\x03")

    def test_out_of_range_info_code_is_rejected(self):
        with self.assertRaises(ValueError):
            build_info_request(0x100)


class TestReplyValidation(unittest.TestCase):
    def test_known_replies_are_valid(self):
        for hexstr in (
            FRAME_03_HEX,
            FRAME_03_ALT_HEX,
            FRAME_09_OFF_HEX,
            FRAME_09_ACTIVE_HEX,
        ):
            self.assertTrue(valid_cn105_reply(bytes.fromhex(hexstr)), hexstr)

    def test_empty_and_none_are_invalid(self):
        self.assertFalse(valid_cn105_reply(None))
        self.assertFalse(valid_cn105_reply(b""))

    def test_too_short_for_a_header_is_invalid(self):
        self.assertFalse(valid_cn105_reply(b"\xfc\x62\x01\x30\x10"))

    def test_wrong_header_is_invalid(self):
        frame = bytearray(bytes.fromhex(FRAME_03_HEX))
        frame[0] = 0xFB
        self.assertFalse(valid_cn105_reply(bytes(frame)))

    def test_wrong_subheader_is_invalid(self):
        frame = bytearray(bytes.fromhex(FRAME_03_HEX))
        frame[2] = 0x02
        self.assertFalse(valid_cn105_reply(bytes(frame)))

    def test_truncated_payload_is_invalid(self):
        self.assertFalse(valid_cn105_reply(bytes.fromhex(FRAME_03_HEX)[:-4]))

    def test_bad_checksum_is_invalid(self):
        frame = bytearray(bytes.fromhex(FRAME_03_HEX))
        frame[-1] ^= 0xFF
        self.assertFalse(valid_cn105_reply(bytes(frame)))

    def test_trailing_bytes_are_tolerated(self):
        # The readback buffer sometimes returns more than one frame.
        frame = bytes.fromhex(FRAME_03_HEX) + bytes.fromhex(FRAME_09_OFF_HEX)
        self.assertTrue(valid_cn105_reply(frame))


class TestIsInfoReply(unittest.TestCase):
    def test_matching_code_is_a_reply(self):
        self.assertTrue(is_info_reply(bytes.fromhex(FRAME_03_HEX), 0x03))

    def test_other_code_is_not_a_reply(self):
        self.assertFalse(is_info_reply(bytes.fromhex(FRAME_03_HEX), 0x09))

    def test_request_frame_is_not_a_reply(self):
        # A 0x42 request carrying code 0x03 must not pass as its own answer.
        self.assertFalse(is_info_reply(build_info_request(0x03), 0x03))

    def test_invalid_frame_is_not_a_reply(self):
        self.assertFalse(is_info_reply(b"\xfc\x62", 0x03))


class TestDecodeTemperatures(unittest.TestCase):
    def _decode(self, frame):
        return decode_info_reply(frame, InfoCode.TEMPERATURES)

    def test_known_frame(self):
        self.assertEqual(
            self._decode(bytes.fromhex(FRAME_03_HEX)),
            {
                "room_temperature": 22.0,
                "outdoor_temperature": 27.0,
                "compressor_runtime_minutes": 137918,
            },
        )

    def test_second_known_frame(self):
        result = self._decode(bytes.fromhex(FRAME_03_ALT_HEX))
        self.assertEqual(result["room_temperature"], 23.0)
        self.assertEqual(result["outdoor_temperature"], 24.0)

    def test_outdoor_encoding(self):
        result = self._decode(make_0x03_reply(outdoor_byte=0xB6))
        self.assertEqual(result["outdoor_temperature"], 27.0)

    def test_outdoor_unavailable_bytes(self):
        # Many outdoor units send 0x00 or 0x01 while the compressor is idle.
        for byte in (0x00, 0x01):
            self.assertIsNone(
                self._decode(make_0x03_reply(outdoor_byte=byte))["outdoor_temperature"]
            )

    def test_room_prefers_encoding_b(self):
        result = self._decode(make_0x03_reply(room_b_byte=0xAC, legacy_byte=0x05))
        self.assertEqual(result["room_temperature"], 22.0)

    def test_room_falls_back_to_legacy_map(self):
        result = self._decode(make_0x03_reply(room_b_byte=0x00, legacy_byte=0x0C))
        self.assertEqual(result["room_temperature"], 22)

    def test_room_out_of_legacy_range(self):
        result = self._decode(make_0x03_reply(room_b_byte=0x00, legacy_byte=0x40))
        self.assertIsNone(result["room_temperature"])

    def test_runtime_counter_is_24_bit_big_endian(self):
        self.assertEqual(
            self._decode(bytes.fromhex(FRAME_03_HEX))["compressor_runtime_minutes"],
            (0x02 << 16) | (0x1A << 8) | 0xBE,
        )

    def test_short_frame_degrades_every_field(self):
        # Valid 0x03 reply but too short to hold anything, so every value is None.
        self.assertEqual(
            self._decode(make_short_reply(0x03)),
            {
                "room_temperature": None,
                "outdoor_temperature": None,
                "compressor_runtime_minutes": None,
            },
        )

    def test_reply_for_another_code_yields_all_none(self):
        result = self._decode(bytes.fromhex(FRAME_09_ACTIVE_HEX))
        self.assertEqual(set(result), set(TELEMETRY_KEYS[InfoCode.TEMPERATURES]))
        self.assertTrue(all(value is None for value in result.values()))


class TestDecodeCompressor(unittest.TestCase):
    def _decode(self, frame):
        return decode_info_reply(frame, InfoCode.COMPRESSOR)

    def test_operating_and_frequency(self):
        self.assertEqual(
            self._decode(make_0x06_reply(freq_byte=0x2A, operating_byte=0x01)),
            {"operating": True, "compressor_frequency": 0x2A},
        )

    def test_standby_zeroes_the_frequency(self):
        # Some units report noise on the frequency byte while idle.
        self.assertEqual(
            self._decode(make_0x06_reply(freq_byte=0x2A, operating_byte=0x00)),
            {"operating": False, "compressor_frequency": 0},
        )

    def test_short_frame_degrades_every_field(self):
        self.assertEqual(
            self._decode(make_short_reply(0x06)),
            {"operating": None, "compressor_frequency": None},
        )

    def test_reply_for_another_code_yields_all_none(self):
        result = self._decode(make_0x09_reply())
        self.assertTrue(all(value is None for value in result.values()))


class TestDecodeSubMode(unittest.TestCase):
    def _decode(self, frame):
        return decode_info_reply(frame, InfoCode.SUB_MODE)

    def test_off_frame(self):
        self.assertEqual(
            self._decode(bytes.fromhex(FRAME_09_OFF_HEX)),
            {"sub_mode": "OFF", "stage": "IDLE", "auto_sub_mode": "AUTO_INACTIVE"},
        )

    def test_active_frame(self):
        self.assertEqual(
            self._decode(bytes.fromhex(FRAME_09_ACTIVE_HEX)),
            {"sub_mode": "NORMAL", "stage": "GENTLE", "auto_sub_mode": "AUTO_INACTIVE"},
        )

    def test_sub_mode_names(self):
        for byte, name in ((0x00, "NORMAL"), (0x02, "DEFROST"), (0x10, "OFF")):
            self.assertEqual(
                self._decode(make_0x09_reply(sub_byte=byte))["sub_mode"], name
            )

    def test_stage_names(self):
        for byte, name in ((0x00, "IDLE"), (0x03, "MEDIUM"), (0x06, "DIFFUSE")):
            self.assertEqual(
                self._decode(make_0x09_reply(stage_byte=byte))["stage"], name
            )

    def test_auto_sub_mode_covers_both_protocol_generations(self):
        for byte, name in AUTO_SUB_MODE_NAMES.items():
            self.assertEqual(
                self._decode(make_0x09_reply(auto_byte=byte))["auto_sub_mode"], name
            )

    def test_unrecognized_enum_byte_is_none(self):
        # Nothing is remembered between calls, so there is no old value to reuse.
        result = self._decode(
            make_0x09_reply(sub_byte=0x7F, stage_byte=0x7F, auto_byte=0x7F)
        )
        self.assertEqual(
            result, {"sub_mode": None, "stage": None, "auto_sub_mode": None}
        )

    def test_short_frame_degrades_every_field(self):
        self.assertEqual(
            self._decode(make_short_reply(0x09)),
            {"sub_mode": None, "stage": None, "auto_sub_mode": None},
        )


class TestDecodeInfoReplyContract(unittest.TestCase):
    def test_absent_and_invalid_frames_still_return_the_key_set(self):
        for frame in (None, b"", b"\xfc\x62", build_info_request(0x03)):
            result = decode_info_reply(frame, InfoCode.TEMPERATURES)
            self.assertEqual(set(result), set(TELEMETRY_KEYS[InfoCode.TEMPERATURES]))
            self.assertTrue(all(value is None for value in result.values()), frame)

    def test_unknown_code_raises(self):
        with self.assertRaises(ValueError):
            decode_info_reply(bytes.fromhex(FRAME_03_HEX), 0x11)

    def test_telemetry_keys_match_what_decoders_return(self):
        for code, keys in TELEMETRY_KEYS.items():
            self.assertEqual(set(decode_info_reply(None, code)), set(keys))

    def test_default_codes_exclude_the_compressor_code(self):
        self.assertEqual(DEFAULT_INFO_CODES, (InfoCode.TEMPERATURES, InfoCode.SUB_MODE))
        self.assertNotIn(InfoCode.COMPRESSOR, DEFAULT_INFO_CODES)


class TestCompressorActivityEstimator(unittest.TestCase):
    def setUp(self):
        self.est = CompressorActivityEstimator(min_interval=70.0)

    def test_starts_undetermined(self):
        self.assertIsNone(self.est.running)

    def test_first_sample_is_undetermined(self):
        self.assertIsNone(self.est.update(100, now=1000.0))

    def test_missing_sample_is_a_noop(self):
        self.est.update(100, now=1000.0)
        self.assertIsNone(self.est.update(None, now=2000.0))

    def test_counter_advanced_means_running(self):
        self.est.update(100, now=1000.0)
        self.assertIs(self.est.update(101, now=1010.0), True)
        self.assertIs(self.est.running, True)

    def test_flat_counter_inside_the_window_stays_undetermined(self):
        self.est.update(100, now=1000.0)
        self.assertIsNone(self.est.update(100, now=1030.0))

    def test_flat_counter_past_the_window_means_not_running(self):
        self.est.update(100, now=1000.0)
        self.assertIs(self.est.update(100, now=1071.0), False)

    def test_baseline_is_kept_so_the_window_grows(self):
        self.est.update(100, now=1000.0)
        # Three unchanged readings in a row. The 1000.0 reading is kept, so by
        # 1071.0 enough time has passed to answer.
        self.est.update(100, now=1030.0)
        self.est.update(100, now=1060.0)
        self.assertIs(self.est.update(100, now=1071.0), False)

    def test_previous_answer_is_held_while_undetermined(self):
        self.est.update(100, now=1000.0)
        self.est.update(101, now=1010.0)
        self.assertIs(self.est.update(101, now=1020.0), True)

    def test_reset_forgets_everything(self):
        self.est.update(100, now=1000.0)
        self.est.update(101, now=1010.0)
        self.est.reset()
        self.assertIsNone(self.est.running)
        self.assertIsNone(self.est.update(101, now=1020.0))

    def test_custom_min_interval(self):
        est = CompressorActivityEstimator(min_interval=10.0)
        est.update(100, now=1000.0)
        self.assertIs(est.update(100, now=1011.0), False)


if __name__ == "__main__":
    unittest.main()
