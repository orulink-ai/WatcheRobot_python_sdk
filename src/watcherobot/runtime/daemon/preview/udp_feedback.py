"""Authenticated completion feedback for the latest-frame UDP preview."""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass


MAGIC = b"FTA1"
VERSION = 1
PREFIX = struct.Struct("<4sBBHII")
PREFIX_SIZE = PREFIX.size
AUTH_TAG_SIZE = 16
PACKET_SIZE = PREFIX_SIZE + AUTH_TAG_SIZE


class FaceTrackingUdpFeedbackError(ValueError):
    """The feedback packet violates the authenticated ACK contract."""


@dataclass(frozen=True)
class PreviewAck:
    stream_id: int
    sequence: int


def encode_preview_ack(
    *, session_key: bytes, stream_id: int, sequence: int
) -> bytes:
    _validate_key(session_key)
    prefix = PREFIX.pack(
        MAGIC,
        VERSION,
        0,
        PACKET_SIZE,
        stream_id & 0xFFFFFFFF,
        sequence & 0xFFFFFFFF,
    )
    auth_tag = hmac.new(session_key, prefix, hashlib.sha256).digest()[:AUTH_TAG_SIZE]
    return prefix + auth_tag


def decode_preview_ack(data: bytes, *, session_key: bytes) -> PreviewAck:
    _validate_key(session_key)
    if len(data) != PACKET_SIZE:
        raise FaceTrackingUdpFeedbackError("invalid ACK packet size")
    magic, version, flags, packet_size, stream_id, sequence = PREFIX.unpack_from(data)
    if (
        magic != MAGIC
        or version != VERSION
        or flags != 0
        or packet_size != PACKET_SIZE
    ):
        raise FaceTrackingUdpFeedbackError("unsupported ACK header")
    expected = hmac.new(session_key, data[:PREFIX_SIZE], hashlib.sha256).digest()[
        :AUTH_TAG_SIZE
    ]
    if not hmac.compare_digest(data[PREFIX_SIZE:], expected):
        raise FaceTrackingUdpFeedbackError("invalid ACK authentication tag")
    return PreviewAck(stream_id=stream_id, sequence=sequence)


def _validate_key(session_key: bytes) -> None:
    if len(session_key) != 32:
        raise FaceTrackingUdpFeedbackError("session key must contain 32 bytes")
