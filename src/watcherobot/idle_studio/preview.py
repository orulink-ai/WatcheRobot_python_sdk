"""Bounded ANPK v2 decoding for previews of device SD animations."""
from io import BytesIO
import struct

from PIL import Image


def animation_frames(data: bytes):
    if not 28 <= len(data) <= 16 * 1024 * 1024:
        raise ValueError('动画文件大小无效')
    magic, version, width, height, count, loop, reserved, delay, toc, content, raw_size = struct.unpack_from('<4sHHHHBBHIII', data)
    if (magic != b'ANPK' or version != 2 or loop not in (0, 1) or reserved != 0
            or not 1 <= width <= 512 or not 1 <= height <= 512
            or not 1 <= count <= 600 or width * height * count > 25_000_000
            or raw_size != width * height * 2 or toc < 28
            or toc + count * 12 > content or content > len(data)):
        raise ValueError('不支持或损坏的动画文件头')
    frames, durations = [], []
    for index in range(count):
        offset, length, duration, flags = struct.unpack_from('<IIHH', data, toc + index * 12)
        start = content + offset
        if flags not in (0, 1) or not length or start + length > len(data):
            raise ValueError('动画帧范围无效')
        frame = data[start:start + length]
        if flags == 1:
            if length < 2:
                raise ValueError('动画调色板缺失')
            colors = struct.unpack_from('<H', frame)[0]
            palette_end = 2 + colors * 2
            if not 1 <= colors <= 256 or length != palette_end + width * height:
                raise ValueError('动画调色板大小无效')
            indices = frame[palette_end:]
            if max(indices) >= colors:
                raise ValueError('动画调色板索引无效')
            palette = [frame[2 + i * 2:4 + i * 2] for i in range(colors)]
            frame = b''.join(palette[i] for i in indices)
        elif length != raw_size:
            raise ValueError('动画像素长度无效')
        rgb = bytearray(width * height * 3)
        for pixel, (value,) in enumerate(struct.iter_unpack('>H', frame)):
            red, green, blue = (value >> 11) & 31, (value >> 5) & 63, value & 31
            rgb[pixel * 3:pixel * 3 + 3] = bytes(((red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2)))
        frames.append(Image.frombytes('RGB', (width, height), bytes(rgb)))
        durations.append(max(20, min(5000, duration or delay or 100)))
    return frames, durations


def animation_gif(data: bytes) -> bytes:
    frames, durations = animation_frames(data)
    result = BytesIO()
    frames[0].save(result, format='GIF', save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, disposal=2)
    return result.getvalue()
