"""Convert Creator source media into self-contained device work assets."""

from __future__ import annotations

import base64
import hashlib
import re
import struct
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

import av
from av.audio.resampler import AudioResampler
from PIL import Image, ImageSequence, UnidentifiedImageError


DISPLAY_SIZE = 206
MAX_SOURCE_BYTES_BY_KIND = {
    "expression": 2 * 1024 * 1024,
    "sound": 4 * 1024 * 1024,
}
MAX_ANIMATION_FRAMES = 120
MAX_DEVICE_ASSET_BYTES = 16 * 1024 * 1024
MAX_AUDIO_DURATION_SECONDS = 30
ANIMPACK_HEADER = "<4sHHHHBBHIII"
ANIMPACK_FRAME = "<IIHH"
ANIMPACK_INDEXED8 = 0x0001
CUSTOM_ID = re.compile(r"^custom-(?:expression|sound)-[A-Za-z0-9_-]{1,96}$")
DATA_URL = re.compile(r"^data:([^;,]+);base64,([A-Za-z0-9+/=\r\n]+)$")


class WorkAssetError(ValueError):
    """A Creator source asset is unsafe or cannot be converted for the device."""


@dataclass(frozen=True)
class ConvertedWorkAsset:
    source_id: str
    kind: str
    resource_id: str
    device_path: str
    device_payload: bytes
    device_asset: dict[str, Any]
    source_path: str
    source_payload: bytes
    creator_asset: dict[str, Any]


def _decode_data_url(value: Any, expected_mime: str, *, max_bytes: int) -> tuple[str, bytes]:
    if not isinstance(value, str):
        raise WorkAssetError("自定义素材缺少 dataUrl。")
    match = DATA_URL.fullmatch(value)
    if match is None:
        raise WorkAssetError("自定义素材 dataUrl 必须是 Base64 数据。")
    mime_type = match.group(1).lower()
    if expected_mime and mime_type != expected_mime.lower():
        raise WorkAssetError("自定义素材 MIME 类型与 dataUrl 不一致。")
    try:
        payload = base64.b64decode(match.group(2), validate=True)
    except ValueError as exc:
        raise WorkAssetError("自定义素材 Base64 内容无效。") from exc
    if not 0 < len(payload) <= max_bytes:
        raise WorkAssetError(f"自定义素材为空或超过 {max_bytes // (1024 * 1024)} MB。")
    return mime_type, payload


def _safe_source_extension(file_name: str, mime_type: str, kind: str) -> str:
    extension = PurePosixPath(file_name.replace("\\", "/")).suffix.lower()
    accepted = {
        "expression": {".gif", ".png", ".jpg", ".jpeg", ".webp"},
        "sound": {".wav", ".mp3", ".ogg"},
    }[kind]
    if extension in accepted:
        return extension
    fallback = {
        "image/gif": ".gif", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
        "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mpeg": ".mp3", "audio/ogg": ".ogg",
    }.get(mime_type)
    if fallback not in accepted:
        raise WorkAssetError("自定义素材文件格式不受支持。")
    return fallback


def _fit_frame(frame: Image.Image) -> Image.Image:
    rgba = frame.convert("RGBA")
    rgba.thumbnail((DISPLAY_SIZE, DISPLAY_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (DISPLAY_SIZE, DISPLAY_SIZE), (0, 0, 0, 255))
    canvas.alpha_composite(rgba, ((DISPLAY_SIZE - rgba.width) // 2, (DISPLAY_SIZE - rgba.height) // 2))
    return canvas


def _rgb565_swapped(frame: Image.Image) -> bytes:
    payload = bytearray(DISPLAY_SIZE * DISPLAY_SIZE * 2)
    rgba = frame.convert("RGBA").tobytes()
    output_offset = 0
    for pixel_offset in range(0, len(rgba), 4):
        red, green, blue, alpha = rgba[pixel_offset:pixel_offset + 4]
        if alpha != 255:
            red = red * alpha // 255
            green = green * alpha // 255
            blue = blue * alpha // 255
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        payload[output_offset] = value >> 8
        payload[output_offset + 1] = value & 0xFF
        output_offset += 2
    return bytes(payload)


def _indexed8(rgb565: bytes) -> bytes | None:
    palette: list[bytes] = []
    indexes: dict[bytes, int] = {}
    pixels = bytearray()
    for offset in range(0, len(rgb565), 2):
        color = rgb565[offset:offset + 2]
        index = indexes.get(color)
        if index is None:
            if len(palette) >= 256:
                return None
            index = len(palette)
            indexes[color] = index
            palette.append(color)
        pixels.append(index)
    return struct.pack("<H", len(palette)) + b"".join(palette) + bytes(pixels)


def _animation_payload(source: bytes) -> bytes:
    try:
        with Image.open(BytesIO(source)) as image:
            fallback_delay = int(image.info.get("duration") or 100)
            frames = [
                (_fit_frame(frame.copy()), max(20, min(5000, int(frame.info.get("duration") or fallback_delay))))
                for frame in ImageSequence.Iterator(image)
            ]
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise WorkAssetError(f"无法解析自定义图片或 GIF：{exc}") from exc
    if not frames or len(frames) > MAX_ANIMATION_FRAMES:
        raise WorkAssetError(f"自定义 GIF 帧数必须在 1 到 {MAX_ANIMATION_FRAMES} 之间。")
    encoded: list[tuple[bytes, int, int]] = []
    for frame, delay in frames:
        raw = _rgb565_swapped(frame)
        indexed = _indexed8(raw)
        encoded.append((indexed if indexed is not None else raw, ANIMPACK_INDEXED8 if indexed is not None else 0, delay))
    toc_offset = struct.calcsize(ANIMPACK_HEADER)
    frame_descriptor_size = struct.calcsize(ANIMPACK_FRAME)
    payload_offset = toc_offset + len(encoded) * frame_descriptor_size
    cursor = 0
    descriptors = bytearray()
    frame_payloads = bytearray()
    for payload, flags, delay in encoded:
        descriptors.extend(struct.pack(ANIMPACK_FRAME, cursor, len(payload), delay, flags))
        frame_payloads.extend(payload)
        cursor += len(payload)
    result = struct.pack(
        ANIMPACK_HEADER, b"ANPK", 2, DISPLAY_SIZE, DISPLAY_SIZE, len(encoded), 0, 0,
        encoded[0][2], toc_offset, payload_offset, DISPLAY_SIZE * DISPLAY_SIZE * 2,
    ) + bytes(descriptors) + bytes(frame_payloads)
    if len(result) > MAX_DEVICE_ASSET_BYTES:
        raise WorkAssetError("转换后的 GIF 超过 16 MB，请减少帧数或尺寸。")
    return result


def _audio_payload(source: bytes) -> bytes:
    output = bytearray()
    try:
        with av.open(BytesIO(source), mode="r") as container:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise WorkAssetError("音频文件没有可解码的音轨。")
            resampler = AudioResampler(format="s16", layout="mono", rate=24000)
            decoded = container.decode(audio=stream.index)
            for source_frame in decoded:
                for frame in resampler.resample(source_frame):
                    output.extend(bytes(frame.planes[0])[:frame.samples * 2])
                    if len(output) > MAX_AUDIO_DURATION_SECONDS * 24000 * 2:
                        raise WorkAssetError("自定义音频不能超过 30 秒。")
            for frame in resampler.resample(None):
                output.extend(bytes(frame.planes[0])[:frame.samples * 2])
    except WorkAssetError:
        raise
    except (av.error.FFmpegError, EOFError, OSError, ValueError) as exc:
        raise WorkAssetError(f"无法解码自定义音频：{exc}") from exc
    if not output:
        raise WorkAssetError("自定义音频没有可播放的 PCM 数据。")
    return bytes(output)


def convert_creator_asset(value: Any) -> ConvertedWorkAsset:
    if not isinstance(value, dict):
        raise WorkAssetError("自定义素材格式无效。")
    source_id = value.get("id")
    kind = value.get("kind")
    if not isinstance(source_id, str) or CUSTOM_ID.fullmatch(source_id) is None:
        raise WorkAssetError("自定义素材 ID 格式无效。")
    if kind not in {"expression", "sound"} or not source_id.startswith(f"custom-{kind}-"):
        raise WorkAssetError("自定义素材 ID 与类型不一致。")
    file_name = value.get("fileName")
    mime_type = value.get("mimeType")
    if not isinstance(file_name, str) or not file_name.strip() or not isinstance(mime_type, str):
        raise WorkAssetError("自定义素材缺少文件名或 MIME 类型。")
    mime_type, source = _decode_data_url(
        value.get("dataUrl"),
        mime_type,
        max_bytes=MAX_SOURCE_BYTES_BY_KIND[kind],
    )
    extension = _safe_source_extension(file_name, mime_type, kind)
    source_sha256 = hashlib.sha256(source).hexdigest()
    source_path = f"sources/{kind}/{source_sha256}{extension}"
    if kind == "expression":
        device = _animation_payload(source)
        directory, suffix, asset_key, asset_kind, asset_format = "anim", ".animpack", "animation", "anim", "animpack-v2"
    else:
        device = _audio_payload(source)
        directory, suffix, asset_key, asset_kind, asset_format = "sfx", ".pcm", "sound", "sfx", "pcm-s16le-24khz-mono"
    digest = hashlib.sha256(device).hexdigest()
    resource_id = f"w{kind[0]}_{digest[:16]}"
    device_path = f"{directory}/{digest}{suffix}"
    device_asset = {
        "kind": asset_kind,
        "path": device_path,
        "sha256": digest,
        "size": len(device),
        "format": asset_format,
    }
    creator_asset = {
        "id": source_id,
        "kind": kind,
        "name": str(value.get("name") or file_name)[:64],
        "fileName": file_name[:128],
        "mimeType": mime_type,
        "size": len(source),
        "sourcePath": source_path,
        "sha256": source_sha256,
        "deviceResourceId": resource_id,
        "deviceAssetKind": asset_key,
    }
    return ConvertedWorkAsset(
        source_id, kind, resource_id, device_path, device, device_asset,
        source_path, source, creator_asset,
    )
