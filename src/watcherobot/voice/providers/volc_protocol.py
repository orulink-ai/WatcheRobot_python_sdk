"""Bounded decoder for Volcengine's version 1 ASR wire protocol."""
from __future__ import annotations

import gzip
import io
import json
import struct

from ..contracts import VoiceError

MAX_PACKET = 1024 * 1024


def encode_request(payload: bytes, sequence: int, *, audio: bool = False, final: bool = False) -> bytes:
    compressed = gzip.compress(payload)
    header = bytes([0x11, (0x20 if audio else 0x10) | (3 if final else 1), 0x01 if audio else 0x11, 0])
    return header + struct.pack('>iI', -sequence if final else sequence, len(compressed)) + compressed


def decode_response(packet: bytes | str) -> tuple[str, bool]:
    try:
        if not isinstance(packet, bytes) or len(packet) < 8 or packet[0] >> 4 != 1:
            raise ValueError
        kind, flags = packet[1] >> 4, packet[1] & 15
        offset = (packet[0] & 15) * 4
        if offset < 4 or offset > len(packet):
            raise ValueError
        if kind == 15:
            raise VoiceError('ASR 火山服务错误；请检查资源和凭据')
        if kind != 9 or packet[2] >> 4 != 1 or packet[2] & 15 not in (0, 1):
            raise ValueError
        offset += (4 if flags & 1 else 0) + (4 if flags & 4 else 0)
        size = struct.unpack('>I', packet[offset:offset + 4])[0]
        payload = packet[offset + 4:]
        if len(payload) != size or size > MAX_PACKET:
            raise ValueError
        if packet[2] & 15 == 1:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                payload = stream.read(MAX_PACKET + 1)
        if len(payload) > MAX_PACKET:
            raise ValueError
        result = json.loads(payload)
        if result.get('code', 0) not in (0, 1000, 20000000):
            raise VoiceError('ASR 火山识别失败')
        text = result.get('result', {}).get('text', '')
        if not isinstance(text, str):
            raise ValueError
        return text, bool(flags & 2)
    except VoiceError:
        raise
    except Exception:
        raise VoiceError('ASR 无效或不完整的协议帧') from None
