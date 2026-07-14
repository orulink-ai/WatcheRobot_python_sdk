from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import Any, Mapping

PROTOCOL_VERSION = "1.0"
DISCOVERY_PORT = 37021
WEBSOCKET_PORT = 8766

FRAME_AUDIO = 1
FRAME_VIDEO = 2
FRAME_IMAGE = 3
FRAME_OTA = 4
FRAME_APP_PACKAGE = 5

FLAG_FIRST = 1 << 0
FLAG_LAST = 1 << 1
FLAG_KEYFRAME = 1 << 2
FLAG_FRAGMENT = 1 << 3


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class BinaryFrame:
    frame_type: int
    flags: int
    stream_id: int
    sequence: int
    payload: bytes


def build_command(message_type: str, data: Mapping[str, Any], command_id: str) -> str:
    command_data = dict(data)
    command_data["command_id"] = command_id
    return json.dumps(
        {"type": message_type, "code": 0, "data": command_data},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def build_wspk(
    frame_type: int,
    flags: int,
    stream_id: int,
    sequence: int,
    payload: bytes,
) -> bytes:
    """Build the current 16-byte WSPK binary frame."""
    if not 0 <= frame_type <= 0xFF or not 0 <= flags <= 0xFF:
        raise ValueError("frame_type and flags must fit in one byte")
    if not 0 < stream_id <= 0xFFFF:
        raise ValueError("stream_id must be between 1 and 65535")
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("sequence must fit in uint32")
    payload_bytes = bytes(payload)
    return (
        b"WSPK"
        + bytes((frame_type, flags))
        + struct.pack("<HII", stream_id, sequence, len(payload_bytes))
        + payload_bytes
    )


def parse_json_message(raw: str) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid JSON message") from error
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("message envelope requires a string type")
    if not isinstance(message.get("data", {}), dict):
        raise ProtocolError("message envelope data must be an object")
    return message


def parse_wspk(packet: bytes) -> BinaryFrame:
    if len(packet) < 14 or packet[:4] != b"WSPK":
        raise ProtocolError("invalid WSPK header")

    frame_type = packet[4]
    flags = packet[5]

    if len(packet) >= 16:
        stream_id, sequence, payload_size = struct.unpack_from("<HII", packet, 6)
        if payload_size == len(packet) - 16:
            return BinaryFrame(frame_type, flags, stream_id, sequence, packet[16:])

    sequence, payload_size = struct.unpack_from("<II", packet, 6)
    if payload_size != len(packet) - 14:
        raise ProtocolError("WSPK payload length mismatch")
    return BinaryFrame(frame_type, flags, 0, sequence, packet[14:])
