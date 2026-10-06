"""Build and decode raw CN105/ITP serial frames.

Frames reach the indoor unit through the Kumo adapter's
``indoorUnit.settings.rawITPFrame`` node (see :class:`pykumo.cn105_bus.Cn105Bus`).
The wire format is::

    FC | type | 01 30 | payloadLen | payload | checksum

The checksum is ``(0xFC - sum(preceding_bytes)) & 0xFF``. An info request is type
``0x42`` with the info code in payload byte 0, and the reply is type ``0x62``
echoing that code.

Byte numbers below count from the start of the frame, so ``frame[5]`` is the info
code and "byte N" means ``payload[N - 5]``.
"""

from enum import IntEnum

PACKET_HEADER = 0xFC
# Standard CN105 sub-header that follows the type byte on every frame.
PACKET_SUBHEADER = bytes([0x01, 0x30])
# Bytes of framing around the payload: header, type, sub-header, len, checksum.
FRAME_OVERHEAD = 6
# Info request/response type bytes.
INFO_REQUEST_TYPE = 0x42
INFO_RESPONSE_TYPE = 0x62
# Info request payloads are always padded to 16 bytes on the wire.
PAYLOAD_SIZE = 16
# A signed byte is used for the frame length in firmware, so 1..127 is usable.
MAX_FRAME_LEN = 127
# The runtime counter has 1-minute resolution, so samples must be at least this
# far apart before a flat counter means "not running".
RUNTIME_SAMPLE_MIN_INTERVAL_SECONDS = 70.0


class InfoCode(IntEnum):
    """The info codes you can ask a unit for, sent as the first payload byte."""

    # Room temperature, outdoor temperature, compressor runtime counter.
    TEMPERATURES = 0x03
    # Operating flag and compressor frequency. Not every unit has it, and asking
    # one that does not wastes half a minute and disturbs the reads that follow.
    COMPRESSOR = 0x06
    # Sub mode, indoor fan stage, auto sub mode.
    SUB_MODE = 0x09


# Codes every unit we have tried answers. COMPRESSOR is left out on purpose.
DEFAULT_INFO_CODES = (InfoCode.TEMPERATURES, InfoCode.SUB_MODE)

# Sub mode names for the 0x09 response, byte 8.
SUB_MODE_NAMES = {
    0x00: "NORMAL",
    0x01: "WARMUP",
    0x02: "DEFROST",
    0x04: "PREHEAT",
    0x08: "STANDBY",
    0x10: "OFF",
}
# Indoor fan stage names for the 0x09 response, byte 9. This describes the fan,
# not whether the compressor is running.
STAGE_NAMES = {
    0x00: "IDLE",
    0x01: "LOW",
    0x02: "GENTLE",
    0x03: "MEDIUM",
    0x04: "MODERATE",
    0x05: "HIGH",
    0x06: "DIFFUSE",
}
# Auto sub mode names for the 0x09 response, byte 10. Two generations of units
# share this byte: older ones use 0x00..0x03, newer MFZ ones use 0x40/0x41/0x43.
AUTO_SUB_MODE_NAMES = {
    0x00: "AUTO_OFF",
    0x01: "AUTO_COOL",
    0x02: "AUTO_HEAT",
    0x03: "AUTO_LEADER",
    0x40: "AUTO_INACTIVE",
    0x41: "AUTO_IDLE",
    0x43: "AUTO_ACTIVE",
}


def cn105_checksum(data: bytes) -> int:
    """Return the CN105 frame checksum for ``data`` (all preceding bytes)."""
    return (0xFC - sum(data)) & 0xFF


def build_cn105_frame(
    type_byte: int, payload: bytes, pad_to: int = PAYLOAD_SIZE
) -> bytes:
    """Build a whole frame: header, payload, then the checksum.

    ``payload`` gets padded with zeros out to ``pad_to`` bytes, which is how info
    requests (``0x42``) and set commands (``0x41``) look on the wire. Pass
    ``pad_to=0`` for frames with a short payload, like connect.
    """
    if not 0 <= type_byte <= 0xFF:
        raise ValueError("type_byte must be 0..255")
    if not 0 <= pad_to <= PAYLOAD_SIZE:
        raise ValueError(f"pad_to must be 0..{PAYLOAD_SIZE}")
    payload = bytes(payload).ljust(pad_to, b"\x00")
    if len(payload) > PAYLOAD_SIZE:
        raise ValueError(f"payload must be <= {PAYLOAD_SIZE} bytes")
    body = bytes([PACKET_HEADER, type_byte]) + PACKET_SUBHEADER
    body += bytes([len(payload)]) + payload
    return body + bytes([cn105_checksum(body)])


def build_info_request(code: int) -> bytes:
    """Build an info-request frame (type ``0x42``) for the given info ``code``."""
    if not 0 <= code <= 0xFF:
        raise ValueError("code must be 0..255")
    return build_cn105_frame(INFO_REQUEST_TYPE, bytes([code]))


def valid_cn105_reply(frame) -> bool:
    """True if ``frame`` begins with a well-formed frame whose checksum matches.

    Trailing bytes are ignored, since the adapter sometimes hands back more than
    one frame at once.
    """
    if not frame or len(frame) < FRAME_OVERHEAD:
        return False
    if frame[0] != PACKET_HEADER or frame[2:4] != PACKET_SUBHEADER:
        return False
    end = frame[4] + FRAME_OVERHEAD
    if len(frame) < end:
        return False
    return cn105_checksum(frame[: end - 1]) == frame[end - 1]


def is_info_reply(frame, code: int) -> bool:
    """True if ``frame`` is a valid ``0x62`` reply to a request for ``code``."""
    if not valid_cn105_reply(frame):
        return False
    return frame[1] == INFO_RESPONSE_TYPE and frame[5] == code


def _decode_temperatures(frame) -> dict:
    """Read the temperatures and the runtime counter out of a ``0x03`` reply."""
    room = outdoor = runtime = None
    # Byte 10, (b - 128) / 2. Anything <= 1 means the unit has no reading, which
    # many report while the compressor is idle.
    if len(frame) > 10 and frame[10] > 1:
        outdoor = (frame[10] - 128) / 2
    # Byte 11 if it has a value, otherwise the older byte 8 scale (0x00..0x1F is
    # 10..41 C).
    if len(frame) > 11 and frame[11]:
        room = (frame[11] - 128) / 2
    elif len(frame) > 8 and frame[8] <= 0x1F:
        room = 10 + frame[8]
    # 24-bit big-endian across bytes 16..18. It only ticks up while the compressor
    # runs, about once a minute, so it measures compressor time rather than how
    # long the unit has been powered on.
    if len(frame) > 18:
        runtime = (frame[16] << 16) | (frame[17] << 8) | frame[18]
    return {
        "room_temperature": room,
        "outdoor_temperature": outdoor,
        "compressor_runtime_minutes": runtime,
    }


def _decode_compressor(frame) -> dict:
    """Read the operating flag and compressor frequency out of a ``0x06`` reply."""
    operating = frequency = None
    if len(frame) > 9:
        # Byte 9: 1 = compressor running, 0 = standby.
        operating = frame[9] == 1
        # Byte 8, but report 0 while idle since some units put noise here.
        frequency = frame[8] if operating else 0
    return {"operating": operating, "compressor_frequency": frequency}


def _decode_sub_mode(frame) -> dict:
    """Read the sub mode, fan stage, and auto sub mode out of a ``0x09`` reply."""
    return {
        "sub_mode": SUB_MODE_NAMES.get(frame[8]) if len(frame) > 8 else None,
        "stage": STAGE_NAMES.get(frame[9]) if len(frame) > 9 else None,
        "auto_sub_mode": (
            AUTO_SUB_MODE_NAMES.get(frame[10]) if len(frame) > 10 else None
        ),
    }


_DECODERS = {
    InfoCode.TEMPERATURES: _decode_temperatures,
    InfoCode.COMPRESSOR: _decode_compressor,
    InfoCode.SUB_MODE: _decode_sub_mode,
}

# Which fields each info code reports, for callers that need the names before
# reading anything. Built by decoding an empty frame, so it always matches what
# the decoders actually return.
TELEMETRY_KEYS = {code: tuple(decoder(b"")) for code, decoder in _DECODERS.items()}


def decode_info_reply(frame, code: int) -> dict:
    """Decode a reply into a ``{field: value}`` dict.

    Every field for ``code`` is there, ``None`` where the frame has no usable
    value. ``code`` is required because a stale reply in the adapter's buffer would
    otherwise look fresh. Raises ``ValueError`` for a code with no decoder.
    """
    decoder = _DECODERS.get(code)
    if decoder is None:
        raise ValueError(f"no CN105 decoder for info code 0x{code:02x}")
    return decoder(frame if is_info_reply(frame, code) else b"")


class CompressorActivityEstimator:
    """Work out whether the compressor is running from the ``0x03`` runtime counter.

    The counter only moves while the compressor runs, so if it changed between two
    readings at least ``min_interval`` apart, the compressor was running. Used
    where info code ``0x06``, the real operating flag, is unsafe to ask for.

    This is inherently slow, with one to two minutes of delay from the actual
    state, because of how the detection works.
    """

    def __init__(self, min_interval: float = RUNTIME_SAMPLE_MIN_INTERVAL_SECONDS):
        self._min_interval = min_interval
        # The reading we compare against, as (time, runtime minutes).
        self._sample = None
        self._running = None

    @property
    def running(self) -> bool | None:
        """Whether the compressor is running, or ``None`` if we cannot tell yet."""
        return self._running

    def update(self, runtime_minutes, now: float) -> bool | None:
        """Add a counter reading and return whether the compressor is running."""
        if runtime_minutes is None:
            return self._running
        if self._sample is None:
            self._sample = (now, runtime_minutes)
            return self._running
        sampled_at, sampled_minutes = self._sample
        if runtime_minutes == sampled_minutes and now - sampled_at < self._min_interval:
            # Keep the older reading so the gap between them keeps growing.
            return self._running
        self._sample = (now, runtime_minutes)
        self._running = runtime_minutes > sampled_minutes
        return self._running

    def reset(self) -> None:
        """Throw away the stored reading and start over."""
        self._sample = None
        self._running = None
