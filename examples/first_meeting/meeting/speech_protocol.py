"""Volcengine ASR v3 binary messages (application cloud protocol only)."""
import gzip
import json
import struct

from .cloud import CloudError


def encode_request(payload: bytes, sequence: int, *, audio: bool = False, final: bool = False) -> bytes:
    compressed = gzip.compress(payload)
    header = bytes([0x11, (0x20 if audio else 0x10) | (3 if final else 1), 0x01 if audio else 0x11, 0])
    return header + struct.pack('>iI', -sequence if final else sequence, len(compressed)) + compressed


def decode_response(packet: bytes) -> tuple[str, bool]:
    try:
        if len(packet) < 8 or packet[0] >> 4 != 1:
            raise ValueError('header')
        kind, flags = packet[1] >> 4, packet[1] & 15
        offset = (packet[0] & 15) * 4
        if kind == 15:
            code = struct.unpack('>I', packet[offset:offset + 4])[0]
            raise CloudError(f'STT 流式服务错误 {code}')
        if kind != 9:
            raise ValueError('message type')
        if flags & 1:
            offset += 4
        if flags & 4:
            offset += 4
        size = struct.unpack('>I', packet[offset:offset + 4])[0]
        payload = packet[offset + 4:]
        if len(payload) != size:
            raise ValueError('payload length')
        if packet[2] & 15 == 1:
            payload = gzip.decompress(payload)
        result = json.loads(payload)
        if result.get('code', 0) not in (0, 1000, 20000000):
            raise CloudError(f"STT 识别错误 {result['code']}")
        return result.get('result', {}).get('text', ''), bool(flags & 2)
    except (ValueError, KeyError, TypeError, struct.error, OSError, EOFError) as error:
        raise CloudError('STT 返回的协议帧不完整或无效') from error
