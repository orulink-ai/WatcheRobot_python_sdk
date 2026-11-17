"""Bounded decoding of independently compressed USB animation blocks."""
import zlib


def decode_block(data: bytes, expected: int) -> bytes:
    if not 0 < expected <= 32768:
        raise ValueError('动画数据块大小无效')
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(data, expected + 1)
    except zlib.error as exc:
        raise ValueError('动画压缩数据损坏，请重试') from exc
    if len(raw) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('动画压缩数据不完整或超出边界')
    return raw
