from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

from .media import ImageFrame

DisplayImageSource: TypeAlias = (
    bytes | bytearray | memoryview | str | Path | ImageFrame
)

MAX_DISPLAY_JPEG_BYTES = 512 * 1024
MAX_DISPLAY_WIDTH = 412
MAX_DISPLAY_HEIGHT = 412

_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def load_display_jpeg(source: DisplayImageSource) -> bytes:
    if isinstance(source, ImageFrame):
        jpeg = bytes(source.data)
    elif isinstance(source, (str, Path)):
        path = Path(source)
        if path.stat().st_size > MAX_DISPLAY_JPEG_BYTES:
            raise ValueError(
                f"JPEG must not exceed {MAX_DISPLAY_JPEG_BYTES} bytes"
            )
        jpeg = path.read_bytes()
    elif isinstance(source, (bytes, bytearray, memoryview)):
        jpeg = bytes(source)
    else:
        raise TypeError(
            "image must be JPEG bytes, a path, or an ImageFrame"
        )

    _validate_display_jpeg(jpeg)
    return jpeg


def _validate_display_jpeg(jpeg: bytes) -> None:
    if len(jpeg) > MAX_DISPLAY_JPEG_BYTES:
        raise ValueError(
            f"JPEG must not exceed {MAX_DISPLAY_JPEG_BYTES} bytes"
        )
    if len(jpeg) < 4 or jpeg[:2] != b"\xff\xd8":
        raise ValueError("image must be a baseline JPEG")
    if jpeg[-2:] != b"\xff\xd9":
        raise ValueError("JPEG end marker is missing")

    offset = 2
    dimensions: tuple[int, int] | None = None
    while offset < len(jpeg):
        if jpeg[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(jpeg) and jpeg[offset] == 0xFF:
            offset += 1
        if offset >= len(jpeg):
            break

        marker = jpeg[offset]
        offset += 1
        if marker in {0x00, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if marker in {0xD9, 0xDA}:
            break
        if offset + 2 > len(jpeg):
            raise ValueError("JPEG contains a truncated marker")

        segment_length = int.from_bytes(jpeg[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(jpeg):
            raise ValueError("JPEG contains an invalid marker length")
        segment = jpeg[offset + 2 : offset + segment_length]
        if marker in _SOF_MARKERS:
            if marker == 0xC2:
                raise ValueError(
                    "progressive JPEG is not supported by the robot display"
                )
            if marker != 0xC0:
                raise ValueError(
                    "only baseline JPEG is supported by the robot display"
                )
            if len(segment) < 5:
                raise ValueError("JPEG frame header is truncated")
            height = int.from_bytes(segment[1:3], "big")
            width = int.from_bytes(segment[3:5], "big")
            if width <= 0 or height <= 0:
                raise ValueError("JPEG dimensions must be positive")
            dimensions = (width, height)
        offset += segment_length

    if dimensions is None:
        raise ValueError("image must contain a baseline JPEG frame")
    width, height = dimensions
    if width > MAX_DISPLAY_WIDTH or height > MAX_DISPLAY_HEIGHT:
        raise ValueError(
            "JPEG dimensions must not exceed 412x412 pixels"
        )
