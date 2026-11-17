"""Device animation preview decodes bounded data, never trusts file offsets."""
import struct
from io import BytesIO

import pytest
from PIL import Image

from watcherobot.idle_studio.preview import animation_gif


def pack(frames, width=2, height=1):
    header = struct.pack('<4sHHHHBBHIII', b'ANPK', 2, width, height, len(frames), 0, 0, 100, 28, 28 + 12 * len(frames), width * height * 2)
    descriptors, content = bytearray(), bytearray()
    for payload, duration, flags in frames:
        descriptors.extend(struct.pack('<IIHH', len(content), len(payload), duration, flags))
        content.extend(payload)
    return header + descriptors + content


def test_raw_and_indexed_frames_preserve_colors_and_timing():
    indexed = struct.pack('<H', 2) + bytes.fromhex('001fffff') + bytes([0, 1])
    source = pack([(bytes.fromhex('f80007e0'), 80, 0), (indexed, 120, 1)])
    with Image.open(BytesIO(animation_gif(source))) as image:
        assert image.size == (2, 1) and image.n_frames == 2
        assert image.convert('RGB').getpixel((0, 0)) == (255, 0, 0)
        assert image.info['duration'] == 80
        image.seek(1)
        assert image.convert('RGB').getpixel((0, 0)) == (0, 0, 255)
        assert image.info['duration'] == 120


def test_loop_flag_in_device_header_is_supported():
    data = bytearray(pack([(bytes.fromhex('f80007e0'), 100, 0)]))
    data[12] = 1
    assert animation_gif(bytes(data)).startswith(b'GIF')


@pytest.mark.parametrize('mutation', ['truncated', 'offset', 'dimensions', 'flags', 'palette'])
def test_invalid_device_data_is_rejected(mutation):
    data = bytearray(pack([(bytes.fromhex('f80007e0'), 100, 0)]))
    if mutation == 'truncated': data.pop()
    if mutation == 'offset': struct.pack_into('<I', data, 28, 0xffffffff)
    if mutation == 'dimensions': struct.pack_into('<H', data, 6, 65535)
    if mutation == 'flags': struct.pack_into('<H', data, 38, 2)
    if mutation == 'palette':
        data = pack([(struct.pack('<H', 1) + bytes.fromhex('f800') + bytes([0, 1]), 100, 1)])
    with pytest.raises(ValueError): animation_gif(bytes(data))
