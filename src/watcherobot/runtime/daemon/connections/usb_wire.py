"""Content-agnostic WRU1 serial framing shared with device firmware.

Firmware contract (all integers little endian): each packet has the 20-byte
<4sBBHIII header: b"WRU1", kind, flags, fragment_index, frame_id,
payload_len, crc32. Kinds are TEXT=1, BINARY=2, ACK=3; FIRST=1 and LAST=2.
Data starts at index zero with FIRST, increments by one with the same kind/id,
and ends with LAST. A single packet uses FIRST|LAST, including empty frames.
Each payload is at most 1024 bytes; a logical frame is at most 131072 bytes.
CRC is zlib.crc32(payload); text is strict UTF-8 decoded after reassembly.
ACK has flags=0, the acknowledged fragment index/id, length=0 and CRC=0.

Host sends one fragment, waits for its matching ACK (default 1 second), then
waits the configured gap (default 1 ms). It never retransmits; failure closes
the session. Device sends data without waiting for host transport ACKs.
Frame IDs must be nonzero uint32 and unique per direction/session (wrap only
after exhausting uint32). The decoder remembers the most recent 256 completed
or rejected IDs to suppress immediate replays, not arbitrary historical replay.

API: encode_frame(str|bytes, *, frame_id) -> list[bytes];
encode_ack(frame_id, fragment_index) -> bytes;
UsbWireDecoder.feed(bytes, *, now=None) -> list[UsbFrame|UsbAck].
UsbFrame.payload preserves str versus bytes. Feed b"" to enforce deadlines
during idle reads. now is monotonic seconds, injectable for pure tests.
Only validated complete frames dispatch; malformed fragments poison that ID
and abandon assembly. A new FIRST with a different ID can recover. Corrupt
packets with a bounded length are discarded whole, never scanning their
payload for magic. Unaligned bytes resynchronize with a bounded noise budget;
exceeding it raises UsbWireError and the caller must close the connection.
Partial packets and unfinished assemblies expire after fragment_timeout.
No business-message parsing, handshake classification, or device I/O lives here.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import struct
import time
import zlib

MAGIC = b"WRU1"
HEADER = struct.Struct("<4sBBHIII")
HEADER_SIZE = HEADER.size
TEXT, BINARY, ACK = 1, 2, 3
FIRST, LAST = 1, 2
MAX_FRAGMENT_PAYLOAD = 1024
MAX_FRAME_SIZE = 131072
_PACKET_SIZE = HEADER_SIZE + MAX_FRAGMENT_PAYLOAD


class UsbWireError(ValueError):
    """Unrecoverable framing/resynchronization failure."""


@dataclass(frozen=True)
class UsbFrame:
    frame_id: int
    payload: str | bytes


@dataclass(frozen=True)
class UsbAck:
    frame_id: int
    fragment_index: int


def _check_id(frame_id: int) -> None:
    if type(frame_id) is not int or not 1 <= frame_id <= 0xFFFFFFFF:
        raise ValueError("frame_id must be a nonzero uint32")


def encode_frame(payload: str | bytes, *, frame_id: int) -> list[bytes]:
    """Validate a logical payload, then encode ordered packets without I/O."""
    _check_id(frame_id)
    if isinstance(payload, str):
        kind, data = TEXT, payload.encode("utf-8", errors="strict")
    elif isinstance(payload, bytes):
        kind, data = BINARY, payload
    else:
        raise TypeError("USB frame payload must be str or bytes")
    if len(data) > MAX_FRAME_SIZE:
        raise ValueError("USB logical frame exceeds 131072 bytes")
    packets = []
    for index, offset in enumerate(range(0, max(1, len(data)), MAX_FRAGMENT_PAYLOAD)):
        chunk = data[offset : offset + MAX_FRAGMENT_PAYLOAD]
        flags = (FIRST if index == 0 else 0) | (
            LAST if offset + len(chunk) == len(data) else 0
        )
        packets.append(HEADER.pack(
            MAGIC, kind, flags, index, frame_id, len(chunk), zlib.crc32(chunk),
        ) + chunk)
    return packets


def encode_ack(frame_id: int, fragment_index: int) -> bytes:
    """Encode a transport ACK, never a business response."""
    _check_id(frame_id)
    if type(fragment_index) is not int or not 0 <= fragment_index <= 0xFFFF:
        raise ValueError("fragment_index must be a uint16")
    return HEADER.pack(MAGIC, ACK, 0, fragment_index, frame_id, 0, 0)


class UsbWireDecoder:
    """Single-reader incremental decoder with bounded retained state."""

    def __init__(
        self, *, fragment_timeout: float = 1.0, max_noise: int = 4096,
    ) -> None:
        if not math.isfinite(fragment_timeout) or fragment_timeout <= 0:
            raise ValueError("fragment_timeout must be finite and positive")
        if type(max_noise) is not int or max_noise < 0:
            raise ValueError("max_noise must be a nonnegative integer")
        self.fragment_timeout = fragment_timeout
        self.max_noise = max_noise
        self._buffer = bytearray()
        self._pending_since: float | None = None
        self._noise = 0
        self._frame_id = 0
        self._kind = 0
        self._next_index = 0
        self._payload = bytearray()
        self._last_fragment_at: float | None = None
        self._recent: deque[int] = deque()
        self._recent_set: set[int] = set()

    @property
    def buffered_bytes(self) -> int:
        """Bytes retained for an incomplete wire packet (not logical payload)."""
        return len(self._buffer)

    def _remember(self, frame_id: int) -> None:
        if not frame_id or frame_id in self._recent_set:
            return
        if len(self._recent) == 256:
            self._recent_set.remove(self._recent.popleft())
        self._recent.append(frame_id)
        self._recent_set.add(frame_id)

    def _clear_frame(self) -> None:
        self._frame_id = self._kind = self._next_index = 0
        self._payload.clear()
        self._last_fragment_at = None

    def _abort(self, frame_id: int = 0) -> None:
        self._remember(self._frame_id)
        self._remember(frame_id)
        self._clear_frame()

    def _discard_noise(self, count: int) -> None:
        del self._buffer[:count]
        self._noise += count
        if self._noise > self.max_noise:
            self._buffer.clear()
            self._pending_since = None
            self._abort()
            raise UsbWireError("WRU1 resynchronization noise budget exceeded")

    def feed(self, data: bytes, *, now: float | None = None) -> list[UsbFrame | UsbAck]:
        """Consume arbitrary byte splits; idle feeds also expire partial state."""
        stamp = time.monotonic() if now is None else now
        if (
            self._last_fragment_at is not None
            and stamp - self._last_fragment_at >= self.fragment_timeout
        ):
            self._abort()
        if (
            self._pending_since is not None
            and stamp - self._pending_since >= self.fragment_timeout
        ):
            self._buffer.clear()
            self._pending_since = None
            self._abort()
        events: list[UsbFrame | UsbAck] = []
        # Retain one partial packet even for arbitrarily large caller feeds.
        for offset in range(0, len(data), _PACKET_SIZE):
            self._buffer.extend(data[offset : offset + _PACKET_SIZE])
            self._drain(stamp, events)
        return events

    def _drain(self, stamp: float, events: list[UsbFrame | UsbAck]) -> None:
        while self._buffer:
            if not self._buffer.startswith(MAGIC):
                position = self._buffer.find(MAGIC)
                if position >= 0:
                    self._discard_noise(position)
                else:
                    keep = 0
                    for size in (3, 2, 1):
                        if self._buffer.endswith(MAGIC[:size]):
                            keep = size
                            break
                    self._discard_noise(len(self._buffer) - keep)
                    break
            if len(self._buffer) < HEADER_SIZE:
                break
            _, kind, flags, index, frame_id, length, crc = HEADER.unpack_from(self._buffer)
            if length > MAX_FRAGMENT_PAYLOAD:
                self._abort(frame_id)
                self._discard_noise(HEADER_SIZE)
                self._pending_since = None
                continue
            size = HEADER_SIZE + length
            if len(self._buffer) < size:
                break
            payload = bytes(self._buffer[HEADER_SIZE:size])
            del self._buffer[:size]
            self._pending_since = None
            if (
                not frame_id or kind not in (TEXT, BINARY, ACK)
                or flags & ~(FIRST | LAST)
                or (kind == ACK and (flags or length or crc))
                or zlib.crc32(payload) != crc
            ):
                self._abort(frame_id)
                continue
            self._noise = 0
            if kind == ACK:
                events.append(UsbAck(frame_id, index))
                continue
            event = self._accept(kind, flags, index, frame_id, payload, stamp)
            if event is not None:
                events.append(event)
        if self._buffer:
            if self._pending_since is None:
                self._pending_since = stamp
        else:
            self._pending_since = None

    def _accept(
        self, kind: int, flags: int, index: int, frame_id: int,
        payload: bytes, stamp: float,
    ) -> UsbFrame | None:
        if frame_id in self._recent_set:
            return None
        if flags & FIRST:
            if index != 0 or frame_id == self._frame_id:
                self._abort(frame_id)
                return None
            self._abort()
            self._frame_id, self._kind = frame_id, kind
        if (
            self._frame_id != frame_id or self._kind != kind
            or index != self._next_index
            or (not payload and flags != FIRST | LAST)
            or len(self._payload) + len(payload) > MAX_FRAME_SIZE
            or (index == 0xFFFF and not flags & LAST)
        ):
            self._abort(frame_id)
            return None
        self._payload.extend(payload)
        self._next_index += 1
        self._last_fragment_at = stamp
        if not flags & LAST:
            return None
        raw = bytes(self._payload)
        self._remember(frame_id)
        self._clear_frame()
        if kind == TEXT:
            try:
                return UsbFrame(frame_id, raw.decode("utf-8", errors="strict"))
            except UnicodeDecodeError:
                return None
        return UsbFrame(frame_id, raw)
