"""Shared CN105 frame fixtures for the tests."""

from pykumo.cn105 import build_cn105_frame, cn105_checksum

# Full 0x62 reply frames with valid checksums.
FRAME_03_HEX = "fc620130100300000c00b6acadfe4200021abe000025"
FRAME_03_ALT_HEX = "fc620130100300000d00b0aeaefe4200021aa0000045"
FRAME_09_OFF_HEX = "fc620130100900001000400000000000000000000004"
FRAME_09_ACTIVE_HEX = "fc620130100900000002400000000000000000000012"

# Known-good info request frames.
INFO_03_REQUEST_HEX = "fc42013010030000000000000000000000000000007a"
INFO_06_REQUEST_HEX = "fc420130100600000000000000000000000000000077"
INFO_09_REQUEST_HEX = "fc420130100900000000000000000000000000000074"


def make_0x03_reply(outdoor_byte=0x01, room_b_byte=0x00, legacy_byte=0x00, code=0x03):
    """Build a 0x62 reply to a 0x03 request carrying the given temperatures."""
    payload = bytearray(16)
    payload[0] = code  # echoed info code -> raw[5]
    payload[3] = legacy_byte  # raw[8]
    payload[5] = outdoor_byte  # raw[10]
    payload[6] = room_b_byte  # raw[11]
    return build_cn105_frame(0x62, bytes(payload))


def make_0x06_reply(freq_byte=0x00, operating_byte=0x00, code=0x06):
    """Build a valid 0x62 reply to a 0x06 request."""
    payload = bytearray(16)
    payload[0] = code  # echoed info code -> raw[5]
    payload[3] = freq_byte  # raw[8], compressor Hz
    payload[4] = operating_byte  # raw[9], operating flag
    return build_cn105_frame(0x62, bytes(payload))


def make_0x09_reply(sub_byte=0x00, stage_byte=0x00, auto_byte=0x00, code=0x09):
    """Build a valid 0x62 reply to a 0x09 request."""
    payload = bytearray(16)
    payload[0] = code  # echoed info code -> raw[5]
    payload[3] = sub_byte  # raw[8], sub mode
    payload[4] = stage_byte  # raw[9], stage
    payload[5] = auto_byte  # raw[10], auto sub mode
    return build_cn105_frame(0x62, bytes(payload))


def make_short_reply(code):
    """Build a 0x62 reply for ``code`` that is too short to hold any field."""
    body = bytes([0xFC, 0x62, 0x01, 0x30, 0x03, code, 0x00, 0x00])
    return body + bytes([cn105_checksum(body)])
