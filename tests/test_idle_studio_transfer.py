import zlib

import pytest

from watcherobot.idle_studio.transfer import decode_block


def test_compressed_block_preserves_bytes():
    raw = bytes(range(256)) * 128
    assert decode_block(zlib.compress(raw), len(raw)) == raw


@pytest.mark.parametrize('kind', ['truncated', 'oversized', 'trailing', 'short'])
def test_compressed_block_rejects_invalid_stream(kind):
    raw = b'a' * 32768
    data = zlib.compress(raw)
    if kind == 'truncated': data = data[:-1]
    if kind == 'oversized': data = zlib.compress(raw + b'a')
    if kind == 'trailing': data += b'extra'
    if kind == 'short': data = zlib.compress(raw[:-1])
    with pytest.raises(ValueError):
        decode_block(data, len(raw))
