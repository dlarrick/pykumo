"""Send and receive raw CN105 frames through a Kumo adapter."""

import logging
import threading
import time

from .cn105 import (
    DEFAULT_INFO_CODES,
    INFO_RESPONSE_TYPE,
    MAX_FRAME_LEN,
    build_info_request,
    valid_cn105_reply,
)

_LOGGER = logging.getLogger(__name__)

# How often and how long to check for a reply after sending a frame. The adapter
# only holds a reply briefly, so we check repeatedly. The same unit answers some
# codes much faster than others (0x03 in about 3 s, 0x09 in about 11 s) and never
# answers a few, so the window is generous and giving up just returns None.
POLL_INTERVAL_SECONDS = 0.5
REPLY_TIMEOUT_SECONDS = 20.0


class Cn105Bus:
    """Talks to one adapter's ``rawITPFrame`` node, one exchange at a time.

    The adapter has a single buffer for replies, so sending a frame and reading
    its answer happen together under one lock. That only covers threads in this
    process: another program on the network can still overwrite the buffer.
    """

    def __init__(self, unit):
        self._unit = unit
        self._lock = threading.RLock()
        self._answered = set()
        self._unsupported = set()

    @property
    def unsupported_codes(self) -> frozenset:
        """Codes that never answered, which :meth:`read_info` no longer sends."""
        return frozenset(self._unsupported)

    def forget_unsupported(self) -> None:
        """Allow dropped codes to be sent again, e.g. after an adapter reboot."""
        self._unsupported.clear()

    def send(self, frame: bytes, id_byte: int = 1) -> bool:
        """Send a raw frame to the indoor unit.

        ``frame`` is the whole wire frame (``FC | type | 01 30 | len | payload |
        checksum``), 1 to 127 bytes. True if the adapter accepted it.
        """
        frame = bytes(frame)
        length = len(frame)
        if not 1 <= length <= MAX_FRAME_LEN:
            _LOGGER.warning(
                "%s: raw CN105 frame length %d out of range 1..%d",
                self._unit.get_name(),
                length,
                MAX_FRAME_LEN,
            )
            return False
        if not 0 <= id_byte <= 0xFF:
            _LOGGER.warning(
                "%s: CN105 id byte %r out of range", self._unit.get_name(), id_byte
            )
            return False
        command = (
            '{"c":{"indoorUnit":{"settings":{"rawITPFrame":'
            '{"frame":"%s","len":%d,"id":%d}}}}}' % (frame.hex(), length, id_byte)
        ).encode("utf-8")
        with self._lock:
            response = self._unit._request(command)  # pylint: disable=protected-access
        if not response or "_api_error" in response:
            _LOGGER.warning(
                "%s: failed to send raw CN105 frame: %s",
                self._unit.get_name(),
                response,
            )
            return False
        return True

    def read(self) -> bytes | None:
        """Read back the last reply the adapter is holding.

        Returns None if the buffer is empty or the response cannot be parsed.
        """
        query = b'{"c":{"indoorUnit":{"settings":{"rawITPFrame":{}}}}}'
        with self._lock:
            response = self._unit._request(query)  # pylint: disable=protected-access
        try:
            node = response["r"]["indoorUnit"]["settings"]["rawITPFrame"]
        except (KeyError, TypeError):
            return None
        hexstr = node.get("frame") if isinstance(node, dict) else None
        if not hexstr:
            return None
        try:
            return bytes.fromhex(hexstr)
        except ValueError:
            _LOGGER.warning(
                "%s: raw CN105 readback is not valid hex: %r",
                self._unit.get_name(),
                hexstr,
            )
            return None

    def transceive(
        self,
        frame: bytes,
        id_byte: int = 1,
        expect_type: int | None = None,
        expect_code: int | None = None,
        timeout: float = REPLY_TIMEOUT_SECONDS,
    ) -> bytes | None:
        """Send a frame once and wait for the unit's reply.

        Polls the reply buffer, checking the checksum of whatever comes back. Pass
        ``expect_type`` and ``expect_code`` to skip replies that do not match.
        Without them, what is left in the buffer from the previous exchange looks
        like an answer to this one. Returns None if nothing matching arrives.

        ``timeout`` bounds how many times we poll rather than wall-clock time.
        Each poll costs half a second of sleep plus a request, so the real wait
        runs longer than ``timeout``. The whole wait uses one connection.
        """
        with self._lock, self._unit.request_cycle():
            if not self.send(frame, id_byte):
                return None
            poll_count = max(1, int(timeout / POLL_INTERVAL_SECONDS))
            for _ in range(poll_count):
                time.sleep(POLL_INTERVAL_SECONDS)
                reply = self.read()
                if not valid_cn105_reply(reply):
                    continue
                if expect_type is not None and reply[1] != expect_type:
                    continue
                if expect_code is not None and reply[5] != expect_code:
                    continue
                return reply
        _LOGGER.debug(
            "%s: no valid CN105 reply within %.1fs", self._unit.get_name(), timeout
        )
        return None

    def read_info(
        self, code: int, timeout: float = REPLY_TIMEOUT_SECONDS
    ) -> bytes | None:
        """Ask for info ``code`` and return the reply, or None.

        If a code outside :data:`~pykumo.cn105.DEFAULT_INFO_CODES` misses and has
        never answered, the bus stops sending it. Asking a unit for a code it does
        not implement wastes half a minute and disturbs the reads that follow, so
        one miss is enough to give up. A code that has answered before is kept.
        """
        if code in self._unsupported:
            return None
        reply = self.transceive(
            build_info_request(code),
            expect_type=INFO_RESPONSE_TYPE,
            expect_code=code,
            timeout=timeout,
        )
        if reply is not None:
            self._answered.add(code)
            return reply
        if code not in DEFAULT_INFO_CODES and code not in self._answered:
            self._unsupported.add(code)
            _LOGGER.warning(
                "%s: info code 0x%02x did not answer in %.1fs and never has, so "
                "it will not be sent again. This unit probably does not support "
                "it, and CN105 reads may stay stuck until the adapter reboots. "
                "Call forget_unsupported() to try again.",
                self._unit.get_name(),
                code,
                timeout,
            )
        return None
