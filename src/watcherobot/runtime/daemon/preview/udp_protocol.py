"""Bounded UDP framing and latest-frame reassembly for face preview."""

from __future__ import annotations

import struct
import time
import zlib
import hashlib
import hmac
from dataclasses import dataclass
from typing import Callable


MAGIC = b"FTU1"
VERSION = 1
HEADER = struct.Struct("<4sBBHIIHHIIHH16s")
HEADER_SIZE = HEADER.size
AUTHENTICATED_HEADER_SIZE = HEADER_SIZE - 16
DEFAULT_MAX_DATAGRAM_SIZE = 1200
MAX_BUNDLE_SIZE = 64 * 1024
MAX_FRAGMENT_COUNT = 64


class FaceTrackingUdpProtocolError(ValueError):
    """The datagram cannot be handled within the protocol contract."""


@dataclass(frozen=True)
class PreviewDatagram:
    stream_id: int
    sequence: int
    fragment_index: int
    fragment_count: int
    total_length: int
    frame_crc32: int
    payload: bytes


@dataclass(frozen=True)
class CompletedPreviewFrame:
    stream_id: int
    sequence: int
    bundle: bytes


@dataclass
class ReassemblyStats:
    datagrams_received: int = 0
    malformed_datagrams: int = 0
    duplicate_datagrams: int = 0
    stale_datagrams: int = 0
    superseded_frames: int = 0
    timed_out_frames: int = 0
    crc_failures: int = 0
    completed_frames: int = 0


def build_preview_bundle(telemetry: str, image_packet: bytes) -> bytes:
    telemetry_bytes = telemetry.encode("utf-8")
    if not telemetry_bytes or len(telemetry_bytes) > 0xFFFF:
        raise FaceTrackingUdpProtocolError("invalid telemetry length")
    if not image_packet.startswith(b"FTW1"):
        raise FaceTrackingUdpProtocolError("invalid FTW1 image packet")
    bundle = struct.pack("<H", len(telemetry_bytes)) + telemetry_bytes + image_packet
    if len(bundle) > MAX_BUNDLE_SIZE:
        raise FaceTrackingUdpProtocolError("preview bundle is too large")
    return bundle


def parse_preview_bundle(bundle: bytes) -> tuple[str, bytes]:
    if len(bundle) < 2:
        raise FaceTrackingUdpProtocolError("truncated preview bundle")
    telemetry_length = struct.unpack_from("<H", bundle)[0]
    image_offset = 2 + telemetry_length
    if telemetry_length == 0 or image_offset + 24 > len(bundle):
        raise FaceTrackingUdpProtocolError("invalid preview bundle lengths")
    try:
        telemetry = bundle[2:image_offset].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FaceTrackingUdpProtocolError("telemetry is not UTF-8") from exc
    image = bundle[image_offset:]
    if not image.startswith(b"FTW1"):
        raise FaceTrackingUdpProtocolError("missing FTW1 image packet")
    return telemetry, image


def encode_preview_datagrams(
    bundle: bytes,
    *,
    session_key: bytes,
    stream_id: int,
    sequence: int,
    max_datagram_size: int = DEFAULT_MAX_DATAGRAM_SIZE,
) -> list[bytes]:
    if len(session_key) != 32:
        raise FaceTrackingUdpProtocolError("session key must contain 32 bytes")
    if not bundle or len(bundle) > MAX_BUNDLE_SIZE:
        raise FaceTrackingUdpProtocolError("invalid preview bundle size")
    payload_capacity = max_datagram_size - HEADER_SIZE
    if payload_capacity <= 0:
        raise FaceTrackingUdpProtocolError("datagram size cannot hold a header")
    fragment_count = (len(bundle) + payload_capacity - 1) // payload_capacity
    if fragment_count > MAX_FRAGMENT_COUNT:
        raise FaceTrackingUdpProtocolError("too many preview fragments")
    checksum = zlib.crc32(bundle) & 0xFFFFFFFF
    packets: list[bytes] = []
    for index in range(fragment_count):
        payload = bundle[index * payload_capacity : (index + 1) * payload_capacity]
        authenticated_header = struct.pack(
            "<4sBBHIIHHIIHH",
            MAGIC,
            VERSION,
            0,
            HEADER_SIZE,
            stream_id & 0xFFFFFFFF,
            sequence & 0xFFFFFFFF,
            index,
            fragment_count,
            len(bundle),
            checksum,
            len(payload),
            0,
        )
        auth_tag = hmac.new(
            session_key, authenticated_header + payload, hashlib.sha256
        ).digest()[:16]
        packets.append(authenticated_header + auth_tag + payload)
    return packets


def decode_preview_datagram(data: bytes, *, session_key: bytes) -> PreviewDatagram:
    if len(data) < HEADER_SIZE:
        raise FaceTrackingUdpProtocolError("truncated UDP header")
    values = HEADER.unpack_from(data)
    magic, version, flags, header_size = values[:4]
    if magic != MAGIC or version != VERSION or flags != 0 or header_size != HEADER_SIZE:
        raise FaceTrackingUdpProtocolError("unsupported UDP header")
    stream_id, sequence = values[4:6]
    fragment_index, fragment_count, total_length, checksum, payload_length = values[
        6:11
    ]
    auth_tag = values[12]
    if (
        fragment_count == 0
        or fragment_count > MAX_FRAGMENT_COUNT
        or fragment_index >= fragment_count
        or total_length == 0
        or total_length > MAX_BUNDLE_SIZE
        or payload_length == 0
        or HEADER_SIZE + payload_length != len(data)
    ):
        raise FaceTrackingUdpProtocolError("invalid UDP fragment bounds")
    expected_tag = hmac.new(
        session_key,
        data[:AUTHENTICATED_HEADER_SIZE] + data[HEADER_SIZE:],
        hashlib.sha256,
    ).digest()[:16]
    if not hmac.compare_digest(auth_tag, expected_tag):
        raise FaceTrackingUdpProtocolError("invalid UDP authentication tag")
    return PreviewDatagram(
        stream_id=stream_id,
        sequence=sequence,
        fragment_index=fragment_index,
        fragment_count=fragment_count,
        total_length=total_length,
        frame_crc32=checksum,
        payload=data[HEADER_SIZE:],
    )


class FaceTrackingUdpReassembler:
    """Reassemble one newest frame and discard late head-of-line data."""

    def __init__(
        self,
        *,
        session_key: bytes,
        clock: Callable[[], float] = time.monotonic,
        frame_timeout_seconds: float = 0.25,
    ) -> None:
        self._clock = clock
        self._session_key = session_key
        self._timeout = frame_timeout_seconds
        self._current: PreviewDatagram | None = None
        self._started_at = 0.0
        self._parts: dict[int, bytes] = {}
        self._last_completed: tuple[int, int] | None = None
        self.stats = ReassemblyStats()

    def push(self, data: bytes) -> CompletedPreviewFrame | None:
        self.stats.datagrams_received += 1
        try:
            fragment = decode_preview_datagram(data, session_key=self._session_key)
        except FaceTrackingUdpProtocolError:
            self.stats.malformed_datagrams += 1
            return None
        now = self._clock()
        if self._current is not None and now - self._started_at > self._timeout:
            self.stats.timed_out_frames += 1
            self._clear()

        key = (fragment.stream_id, fragment.sequence)
        if self._last_completed is not None and not _key_is_newer(
            key, self._last_completed
        ):
            self.stats.stale_datagrams += 1
            return None
        if self._current is None:
            self._begin(fragment, now)
        else:
            current_key = (self._current.stream_id, self._current.sequence)
            if key != current_key:
                if not _key_is_newer(key, current_key):
                    self.stats.stale_datagrams += 1
                    return None
                self.stats.superseded_frames += 1
                self._begin(fragment, now)
            elif not _same_frame_contract(fragment, self._current):
                self.stats.malformed_datagrams += 1
                self._clear()
                return None

        existing = self._parts.get(fragment.fragment_index)
        if existing is not None:
            if existing == fragment.payload:
                self.stats.duplicate_datagrams += 1
            else:
                self.stats.malformed_datagrams += 1
                self._clear()
            return None
        self._parts[fragment.fragment_index] = fragment.payload
        if len(self._parts) != fragment.fragment_count:
            return None
        bundle = b"".join(
            self._parts[index] for index in range(fragment.fragment_count)
        )
        if (
            len(bundle) != fragment.total_length
            or zlib.crc32(bundle) & 0xFFFFFFFF != fragment.frame_crc32
        ):
            self.stats.crc_failures += 1
            self._clear()
            return None
        completed = CompletedPreviewFrame(
            stream_id=fragment.stream_id,
            sequence=fragment.sequence,
            bundle=bundle,
        )
        self._last_completed = key
        self.stats.completed_frames += 1
        self._clear()
        return completed

    def _begin(self, fragment: PreviewDatagram, now: float) -> None:
        self._current = fragment
        self._started_at = now
        self._parts = {}

    def _clear(self) -> None:
        self._current = None
        self._parts = {}


def _same_frame_contract(left: PreviewDatagram, right: PreviewDatagram) -> bool:
    return (
        left.fragment_count == right.fragment_count
        and left.total_length == right.total_length
        and left.frame_crc32 == right.frame_crc32
    )


def _key_is_newer(candidate: tuple[int, int], reference: tuple[int, int]) -> bool:
    if candidate[0] != reference[0]:
        return True
    delta = (candidate[1] - reference[1]) & 0xFFFFFFFF
    return 0 < delta < 0x80000000
