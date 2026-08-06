"""Versioned user-work documents and portable work-package helpers."""

from __future__ import annotations

import hashlib
import base64
import copy
import gzip
import json
import re
import tarfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from .work_assets import (
    MAX_SOURCE_BYTES_BY_KIND,
    ConvertedWorkAsset,
    WorkAssetError,
    convert_creator_asset,
)


WORK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,22}$")
MAX_WORKS = 64
MAX_WORK_BYTES = 64 * 1024
MAX_WORK_PACKAGE_BYTES = 24 * 1024 * 1024
MAX_WORK_TRACKS = 64
MAX_WORK_DURATION_MS = 10 * 60 * 1000
TRACK_TYPES = {"animation", "action", "sound"}
WORK_FORMAT = "watche-user-work"
WORK_SCHEMA_VERSION = 2
WORK_PACKAGE_FORMAT = "watche-user-work-package"
WORK_PACKAGE_SCHEMA_VERSION = 2
CREATOR_COMPOSITION_KIND = "watcher.creator-composition"
CREATOR_COMPOSITION_VERSION = 2
WORK_PACKAGE_FILES = {"work.json", "work_manifest.json"}
CREATOR_RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
CREATOR_CLIP_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


class WorkDocumentError(ValueError):
    """The SD work document or portable package is malformed or unsafe."""


@dataclass(frozen=True)
class PortableWorkPackage:
    work_id: str
    name: str
    revision: int
    work: dict[str, Any]
    manifest: dict[str, Any]
    files: dict[str, bytes]
    zip_payload: bytes
    serial_payload: bytes

    @property
    def expanded_size_bytes(self) -> int:
        """Exact extracted size of the serial archive, including its manifest."""

        return sum(len(payload) for payload in self.files.values()) + len(_json_bytes(self.manifest))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.serial_payload).hexdigest()

    @property
    def payload(self) -> bytes:
        """Backward-compatible name for the serial tar.gz payload."""

        return self.serial_payload


def _required_string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkDocumentError(f"作品缺少 {key}。")
    return value.strip()


def _json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _serial_archive_payload(files: dict[str, bytes], manifest_payload: bytes) -> bytes:
    """Build a reproducible gzip tar while preserving every validated package file."""

    tar_buffer = BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
        for filename, payload in (*sorted(files.items()), ("work_manifest.json", manifest_payload)):
            info = tarfile.TarInfo(filename)
            info.size = len(payload)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, BytesIO(payload))
    return gzip.compress(tar_buffer.getvalue(), mtime=0)


def _legacy_work_id(name: str) -> str:
    """Return a stable legacy fallback without collapsing non-ASCII names to ``work``."""

    normalized = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    if normalized and normalized[0].isalpha():
        return normalized[:23].rstrip("_")
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"work_{digest}"


def _normalize_work_id(value: Any, name: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return _legacy_work_id(name)
    if not isinstance(value, str) or not WORK_ID_PATTERN.fullmatch(value.strip()):
        raise WorkDocumentError("作品 work_id 格式无效。")
    return value.strip()


def _normalize_creator_clip(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        raise WorkDocumentError(f"作品第 {index + 1} 个时间线片段格式无效。")
    kind = value.get("kind")
    if kind == "light":
        return None
    if kind not in {"expression", "action", "sound"}:
        raise WorkDocumentError(f"作品第 {index + 1} 个时间线片段类型无效。")
    resource_id = value.get("resourceId")
    has_embedded_action = kind == "action" and value.get("actionTracks") is not None
    resource_id_valid = (
        isinstance(resource_id, str)
        and (
            CREATOR_RESOURCE_ID_PATTERN.fullmatch(resource_id) is not None
            if has_embedded_action or resource_id.startswith("custom-")
            else WORK_ID_PATTERN.fullmatch(resource_id) is not None
        )
    )
    if not resource_id_valid:
        raise WorkDocumentError(f"作品第 {index + 1} 个素材 resource_id 格式无效。")
    try:
        start_ms = int(value.get("startMs", 0))
        duration_ms = int(value.get("durationMs", 0))
    except (TypeError, ValueError) as exc:
        raise WorkDocumentError(f"作品第 {index + 1} 个片段时间无效。") from exc
    if start_ms < 0 or duration_ms < 1 or start_ms + duration_ms > MAX_WORK_DURATION_MS:
        raise WorkDocumentError(f"作品第 {index + 1} 个片段时间超出范围。")
    clip_id = value.get("id")
    label = value.get("label")
    normalized_clip_id = clip_id.strip() if isinstance(clip_id, str) and clip_id.strip() else f"clip-{index + 1}"
    if CREATOR_CLIP_ID_PATTERN.fullmatch(normalized_clip_id) is None:
        raise WorkDocumentError(f"作品第 {index + 1} 个片段 clip_id 格式无效。")
    normalized = dict(value)
    normalized.update({
        "id": normalized_clip_id,
        "kind": kind,
        "resourceId": resource_id,
        "label": label.strip() if isinstance(label, str) and label.strip() else resource_id,
        "startMs": start_ms,
        "durationMs": duration_ms,
    })
    return normalized


def _normalize_action_axis(value: Any, axis: str, duration_ms: int) -> list[dict[str, int]]:
    if not isinstance(value, list) or not value:
        return []
    by_frame: dict[int, int] = {}
    for index, keyframe in enumerate(value):
        if not isinstance(keyframe, dict):
            raise WorkDocumentError(f"自定义动作 {axis} 第 {index + 1} 个关键帧格式无效。")
        try:
            time_ms = int(keyframe.get("timeMs", 0))
            angle_deg = int(round(float(keyframe.get("angleDeg"))))
        except (TypeError, ValueError) as exc:
            raise WorkDocumentError(f"自定义动作 {axis} 第 {index + 1} 个关键帧无效。") from exc
        if time_ms < 0 or time_ms > duration_ms or angle_deg < 0 or angle_deg > 180:
            raise WorkDocumentError(f"自定义动作 {axis} 第 {index + 1} 个关键帧超出范围。")
        by_frame[round(time_ms * 50 / 1000)] = angle_deg
    return [{"frame_number": frame, "rotation_angle": by_frame[frame]} for frame in sorted(by_frame)]


def _build_action_asset(clip: dict[str, Any]) -> tuple[str, str, bytes, dict[str, Any]]:
    tracks = clip.get("actionTracks")
    if not isinstance(tracks, dict):
        raise WorkDocumentError("自定义动作缺少 actionTracks。")
    duration_ms = int(clip["durationMs"])
    axes = (
        ("xDeg", "body_x", "MESH", "z"),
        ("yDeg", "head_y", "EMPTY", "x"),
    )
    animated_objects: list[dict[str, Any]] = []
    max_frame = max(1, round(duration_ms * 50 / 1000))
    for axis, object_name, object_type, active_axis in axes:
        keyframes = _normalize_action_axis(tracks.get(axis), axis, duration_ms)
        if not keyframes:
            continue
        max_frame = max(max_frame, keyframes[-1]["frame_number"])
        animated_objects.append({
            "object_name": object_name,
            "object_type": object_type,
            "action_name": str(clip["label"])[:64],
            "keyframe_data": [
                {**keyframe, "active_axis": active_axis}
                for keyframe in keyframes
            ],
        })
    if not animated_objects:
        raise WorkDocumentError("自定义动作没有可写入的舵机关键帧。")
    document = {
        "scene_name": str(clip["label"])[:64],
        "frame_start": 0,
        "frame_end": max_frame,
        "fps": 50,
        "animated_objects": animated_objects,
    }
    payload = _json_bytes(document)
    digest = hashlib.sha256(payload).hexdigest()
    resource_id = f"wa_{digest[:16]}"
    path = f"actions/{digest}.json"
    asset = {
        "kind": "action",
        "path": path,
        "sha256": digest,
        "size": len(payload),
        "format": "firmware-action-json-v1",
    }
    return resource_id, path, payload, asset


def build_portable_work_package(composition: dict[str, Any]) -> PortableWorkPackage:
    if not isinstance(composition, dict):
        raise WorkDocumentError("作品内容必须是 JSON 对象。")
    name_value = composition.get("name")
    name = name_value.strip()[:64] if isinstance(name_value, str) and name_value.strip() else "Untitled work"
    work_id = _normalize_work_id(composition.get("workId") or composition.get("work_id"), name)
    revision_value = composition.get("revision", 1)
    if not isinstance(revision_value, int) or isinstance(revision_value, bool) or revision_value < 1:
        raise WorkDocumentError("作品 revision 必须是大于 0 的整数。")
    raw_clips = composition.get("clips")
    if not isinstance(raw_clips, list) or not raw_clips or len(raw_clips) > MAX_WORK_TRACKS:
        raise WorkDocumentError("作品时间线为空或片段数量超过 64 个。")
    clips = [clip for index, value in enumerate(raw_clips) if (clip := _normalize_creator_clip(value, index))]
    if not clips:
        raise WorkDocumentError("作品没有可写入的表情、动作或音效片段。")

    raw_assets = composition.get("assets", [])
    if not isinstance(raw_assets, list):
        raise WorkDocumentError("作品 assets 必须是数组。")
    converted_assets: dict[str, ConvertedWorkAsset] = {}
    try:
        for raw_asset in raw_assets:
            converted = convert_creator_asset(raw_asset)
            if converted.source_id in converted_assets:
                raise WorkDocumentError(f"作品包含重复的自定义素材：{converted.source_id}")
            converted_assets[converted.source_id] = converted
    except WorkAssetError as exc:
        raise WorkDocumentError(str(exc)) from exc

    used_custom_ids = {
        str(clip["resourceId"])
        for clip in clips
        if clip["kind"] in {"expression", "sound"} and str(clip["resourceId"]).startswith("custom-")
    }
    missing_custom = sorted(used_custom_ids - converted_assets.keys())
    if missing_custom:
        raise WorkDocumentError("作品缺少自定义源素材：" + "、".join(missing_custom))

    kind_map = {
        "expression": ("animation", "anim"),
        "action": ("action", "action"),
        "sound": ("sound", "sfx"),
    }
    tracks: list[dict[str, Any]] = []
    dependencies: dict[str, set[str]] = {}
    package_files: dict[str, bytes] = {}
    bundled_assets: dict[str, dict[str, Any]] = {}
    work_catalog_entries: dict[str, dict[str, Any]] = {}
    used_creator_assets: list[dict[str, Any]] = []
    for clip in clips:
        track_type, asset_kind = kind_map[str(clip["kind"])]
        original_resource_id = str(clip["resourceId"])
        resource_id = original_resource_id
        source = "official"
        converted = converted_assets.get(original_resource_id)
        if converted is not None:
            if (track_type == "animation") != (converted.kind == "expression"):
                raise WorkDocumentError(f"自定义素材 {original_resource_id} 与时间线片段类型不一致。")
            resource_id = converted.resource_id
            source = "work"
            package_files[converted.device_path] = converted.device_payload
            package_files[converted.source_path] = converted.source_payload
            bundled_assets[converted.device_path] = {
                "resource_id": resource_id,
                **converted.device_asset,
            }
            asset_key = "animation" if converted.kind == "expression" else "sound"
            work_catalog_entries[resource_id] = {
                "id": resource_id,
                "display_name": str(clip["label"])[:64],
                "source_record_id": work_id,
                "order": len(work_catalog_entries),
                "assets": {asset_key: converted.device_asset},
            }
            if converted.creator_asset not in used_creator_assets:
                used_creator_assets.append(converted.creator_asset)
        elif track_type == "action" and clip.get("actionTracks") is not None:
            resource_id, path, payload, asset = _build_action_asset(clip)
            source = "work"
            package_files[path] = payload
            bundled_assets[path] = {"resource_id": resource_id, **asset}
            work_catalog_entries[resource_id] = {
                "id": resource_id,
                "display_name": str(clip["label"])[:64],
                "source_record_id": work_id,
                "order": len(work_catalog_entries),
                "assets": {"action": asset},
            }
        else:
            dependencies.setdefault(resource_id, set()).add(track_type)
        tracks.append({
            "clip_id": str(clip["id"]),
            "type": track_type,
            "start_ms": int(clip["startMs"]),
            "duration_ms": int(clip["durationMs"]),
            "asset": {"source": source, "resource_id": resource_id, "kind": asset_kind},
        })
    duration_ms = max(track["start_ms"] + track["duration_ms"] for track in tracks)
    creator = {
        "kind": CREATOR_COMPOSITION_KIND,
        "version": CREATOR_COMPOSITION_VERSION,
        "workId": work_id,
        "revision": revision_value,
        "name": name,
        "exportedAt": str(composition.get("exportedAt") or ""),
        "clips": clips,
    }
    if used_creator_assets:
        creator["assets"] = used_creator_assets
    work = {
        "format": WORK_FORMAT,
        "schema_version": WORK_SCHEMA_VERSION,
        "work_id": work_id,
        "revision": revision_value,
        "name": name,
        "duration_ms": duration_ms,
        "tracks": tracks,
        "creator": creator,
    }
    work_payload = _json_bytes(work)
    if len(work_payload) > MAX_WORK_BYTES:
        raise WorkDocumentError("作品内容超过 64 KB，请减少时间线片段。")
    package_files["work.json"] = work_payload
    if work_catalog_entries:
        package_files["resource_catalog.json"] = _json_bytes({
            "format": "watche-work-resource-catalog",
            "schema_version": 1,
            "work_id": work_id,
            "expressions": list(work_catalog_entries.values()),
        })
    manifest = {
        "format": WORK_PACKAGE_FORMAT,
        "schema_version": WORK_PACKAGE_SCHEMA_VERSION,
        "work_id": work_id,
        "revision": revision_value,
        "name": name,
        "files": [
            {"path": path, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for path, payload in sorted(package_files.items())
        ],
        "dependencies": [
            {"resource_id": resource_id, "kinds": sorted(kinds)}
            for resource_id, kinds in sorted(dependencies.items())
        ],
        "bundled_assets": [bundled_assets[path] for path in sorted(bundled_assets)],
    }
    manifest_payload = _json_bytes(manifest)
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, "w", compression=ZIP_DEFLATED) as archive:
        for filename, payload in (*sorted(package_files.items()), ("work_manifest.json", manifest_payload)):
            info = ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, payload)
    return PortableWorkPackage(
        work_id=work_id,
        name=name,
        revision=revision_value,
        work=work,
        manifest=manifest,
        files=package_files,
        zip_payload=zip_buffer.getvalue(),
        serial_payload=_serial_archive_payload(package_files, manifest_payload),
    )


def _read_package_json(archive: ZipFile, filename: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = archive.read(filename)
        value = json.loads(payload.decode("utf-8-sig"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkDocumentError(f"作品包中的 {filename} 无效。") from exc
    if not isinstance(value, dict):
        raise WorkDocumentError(f"作品包中的 {filename} 必须是 JSON 对象。")
    return value, payload


def read_portable_work_package(path: Path) -> PortableWorkPackage:
    if not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > MAX_WORK_PACKAGE_BYTES:
        raise WorkDocumentError("作品 ZIP 不存在、为空或超过 24 MB。")
    try:
        with ZipFile(path, "r") as archive:
            file_names = [item.filename for item in archive.infolist() if not item.is_dir()]
            names = set(file_names)
            if len(file_names) != len(names):
                raise WorkDocumentError("作品 ZIP 包含重复文件名。")
            work, work_payload = _read_package_json(archive, "work.json")
            manifest, manifest_payload = _read_package_json(archive, "work_manifest.json")
            raw_files = manifest.get("files")
            if not isinstance(raw_files, list) or not raw_files:
                raise WorkDocumentError("作品包清单缺少 files。")
            expected_names = {"work_manifest.json"}
            package_files: dict[str, bytes] = {}
            for item in raw_files:
                relative = item.get("path") if isinstance(item, dict) else None
                if (
                    not isinstance(relative, str)
                    or relative.startswith("/")
                    or "\\" in relative
                    or ".." in Path(relative).parts
                    or relative == "work_manifest.json"
                    or relative in expected_names
                ):
                    raise WorkDocumentError("作品包清单包含不安全或重复的文件路径。")
                expected_names.add(relative)
                try:
                    payload = archive.read(relative)
                except KeyError as exc:
                    raise WorkDocumentError(f"作品 ZIP 缺少 {relative}。") from exc
                if item.get("size") != len(payload) or item.get("sha256") != hashlib.sha256(payload).hexdigest():
                    raise WorkDocumentError(f"作品包 {relative} 的大小或 SHA-256 校验失败。")
                package_files[relative] = payload
            if names != expected_names:
                raise WorkDocumentError("作品 ZIP 文件集合与清单不一致。")
    except (OSError, BadZipFile) as exc:
        raise WorkDocumentError(f"无法读取作品 ZIP：{exc}") from exc
    normalized = normalize_work_document(work, expected_id=str(work.get("work_id") or ""), source="local")
    if (
        manifest.get("format") != WORK_PACKAGE_FORMAT
        or manifest.get("schema_version") not in {1, WORK_PACKAGE_SCHEMA_VERSION}
        or manifest.get("work_id") != normalized["id"]
        or manifest.get("revision") != normalized["revision"]
        or not isinstance(manifest.get("bundled_assets"), list)
    ):
        raise WorkDocumentError("作品包清单版本、标识或素材能力无效。")
    if package_files.get("work.json") != work_payload:
        raise WorkDocumentError("作品包 work.json 未纳入完整性清单。")
    for bundled in manifest["bundled_assets"]:
        bundled_path = bundled.get("path") if isinstance(bundled, dict) else None
        if not isinstance(bundled_path, str) or bundled_path not in package_files:
            raise WorkDocumentError("作品包包含无效的 bundled_assets 声明。")
    # Convert the validated ZIP to the device tar format without rebuilding it
    # from the current Creator schema. This preserves future bundled asset
    # types when an older desktop merely forwards a valid portable package.
    return PortableWorkPackage(
        work_id=normalized["id"],
        name=normalized["name"],
        revision=normalized["revision"],
        work=work,
        manifest=manifest,
        files=package_files,
        zip_payload=path.read_bytes(),
        serial_payload=_serial_archive_payload(package_files, manifest_payload),
    )


def hydrate_creator_assets(work: dict[str, Any], files: dict[str, bytes]) -> dict[str, Any]:
    """Return a copy whose Creator source descriptors include validated data URLs."""

    hydrated = copy.deepcopy(work)
    creator = hydrated.get("creator")
    raw_assets = creator.get("assets", []) if isinstance(creator, dict) else []
    if not isinstance(raw_assets, list):
        raise WorkDocumentError("作品 creator.assets 格式无效。")
    for asset in raw_assets:
        if not isinstance(asset, dict):
            raise WorkDocumentError("作品包含无效的源素材描述。")
        path = asset.get("sourcePath")
        mime_type = asset.get("mimeType")
        kind = asset.get("kind")
        expected_size = asset.get("size")
        expected_sha256 = asset.get("sha256")
        if (
            not isinstance(path, str)
            or not path.startswith("sources/")
            or "\\" in path
            or ".." in Path(path).parts
            or not isinstance(mime_type, str)
            or kind not in MAX_SOURCE_BYTES_BY_KIND
            or not isinstance(expected_size, int)
            or not 0 < expected_size <= MAX_SOURCE_BYTES_BY_KIND[kind]
            or not isinstance(expected_sha256, str)
        ):
            raise WorkDocumentError("作品源素材描述不安全或不完整。")
        payload = files.get(path)
        if (
            payload is None
            or len(payload) != expected_size
            or hashlib.sha256(payload).hexdigest() != expected_sha256
        ):
            raise WorkDocumentError(f"作品源素材 {path} 缺失或校验失败。")
        asset["dataUrl"] = f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"
    return hydrated


def resource_inventory_from_catalog(catalog: Any) -> dict[str, set[str]]:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("expressions"), list):
        raise WorkDocumentError("目标 SD 的 official_catalog.json 格式无效。")
    inventory: dict[str, set[str]] = {}
    for entry in catalog["expressions"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        assets = entry.get("assets")
        if not isinstance(assets, dict) and isinstance(entry.get("device"), dict):
            assets = entry["device"].get("assets")
        if not isinstance(assets, dict):
            continue
        kinds = {kind for kind in TRACK_TYPES if kind in assets and isinstance(assets[kind], dict)}
        inventory[entry["id"]] = kinds
    return inventory


def validate_package_dependencies(package: PortableWorkPackage, catalog: Any) -> None:
    inventory = resource_inventory_from_catalog(catalog)
    missing: list[str] = []
    dependencies = package.manifest.get("dependencies")
    if not isinstance(dependencies, list):
        raise WorkDocumentError("作品包缺少 dependencies。")
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("resource_id"), str):
            raise WorkDocumentError("作品包包含无效素材依赖。")
        resource_id = dependency["resource_id"]
        kinds = dependency.get("kinds")
        if not isinstance(kinds, list) or any(kind not in TRACK_TYPES for kind in kinds):
            raise WorkDocumentError(f"作品 {resource_id} 的素材依赖类型无效。")
        available = inventory.get(resource_id, set())
        for kind in kinds:
            if kind not in available:
                missing.append(f"{resource_id}/{kind}")
    if missing:
        raise WorkDocumentError("目标 SD 缺少作品所需素材：" + "、".join(missing))


def normalize_work_document(
    document: Any,
    *,
    expected_id: str,
    source: str,
    modified_at: str | None = None,
) -> dict[str, Any]:
    if not WORK_ID_PATTERN.fullmatch(expected_id):
        raise WorkDocumentError("作品目录标识不安全。")
    if not isinstance(document, dict) or document.get("schema_version") not in {1, WORK_SCHEMA_VERSION}:
        raise WorkDocumentError("作品文件不是受支持的 v1/v2 格式。")
    schema_version = int(document["schema_version"])
    if schema_version == WORK_SCHEMA_VERSION and document.get("format") != WORK_FORMAT:
        raise WorkDocumentError("作品文件 v2 format 无效。")
    work_id = _required_string(document, "work_id")
    if work_id != expected_id or not WORK_ID_PATTERN.fullmatch(work_id):
        raise WorkDocumentError("作品标识与 SD 目录不一致。")
    name = _required_string(document, "name")
    revision = document.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise WorkDocumentError("作品 revision 无效。")
    duration = document.get("duration_ms")
    tracks = document.get("tracks")
    if not isinstance(duration, int) or not 1 <= duration <= MAX_WORK_DURATION_MS:
        raise WorkDocumentError("作品时长无效。")
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= MAX_WORK_TRACKS:
        raise WorkDocumentError("作品片段为空或数量超出限制。")

    normalized_tracks: list[dict[str, Any]] = []
    counts = {"animation": 0, "action": 0, "sound": 0}
    preview_expression_id: str | None = None
    for index, track in enumerate(tracks):
        if not isinstance(track, dict) or track.get("type") not in TRACK_TYPES:
            raise WorkDocumentError(f"作品第 {index + 1} 个片段类型无效。")
        track_type = str(track["type"])
        start_ms = track.get("start_ms")
        duration_ms = track.get("duration_ms")
        asset = track.get("asset")
        if (
            not isinstance(start_ms, int)
            or start_ms < 0
            or start_ms > duration
            or not isinstance(duration_ms, int)
            or duration_ms < 1
            or start_ms + duration_ms > duration
            or not isinstance(asset, dict)
        ):
            raise WorkDocumentError(f"作品第 {index + 1} 个片段时间或素材无效。")
        resource_id = asset.get("resource_id")
        if not isinstance(resource_id, str) or not WORK_ID_PATTERN.fullmatch(resource_id):
            raise WorkDocumentError(f"作品第 {index + 1} 个片段资源标识无效。")
        clip_id = track.get("clip_id")
        if clip_id is not None and (
            not isinstance(clip_id, str) or CREATOR_CLIP_ID_PATTERN.fullmatch(clip_id) is None
        ):
            raise WorkDocumentError(f"作品第 {index + 1} 个片段 clip_id 无效。")
        if schema_version == WORK_SCHEMA_VERSION:
            expected_kind = {"animation": "anim", "action": "action", "sound": "sfx"}[track_type]
            if asset.get("source") not in {"official", "work"} or asset.get("kind") != expected_kind:
                raise WorkDocumentError(f"作品第 {index + 1} 个片段素材来源或类型无效。")
        counts[track_type] += 1
        if track_type == "animation" and preview_expression_id is None:
            preview_expression_id = resource_id
        normalized_track = {
            "type": track_type,
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            "asset": dict(asset),
        }
        if clip_id is not None:
            normalized_track["clip_id"] = clip_id
        normalized_tracks.append(normalized_track)

    creator: dict[str, Any] | None = None
    if schema_version == WORK_SCHEMA_VERSION:
        raw_creator = document.get("creator")
        if not isinstance(raw_creator, dict):
            raise WorkDocumentError("v2 作品缺少可编辑的 creator 内容。")
        if (
            raw_creator.get("kind") != CREATOR_COMPOSITION_KIND
            or raw_creator.get("version") != CREATOR_COMPOSITION_VERSION
            or raw_creator.get("workId") != work_id
            or raw_creator.get("revision") != revision
            or not isinstance(raw_creator.get("clips"), list)
        ):
            raise WorkDocumentError("v2 作品的 creator 内容与作品标识不一致。")
        creator = dict(raw_creator)

    normalized_document = {
        "format": WORK_FORMAT if schema_version == WORK_SCHEMA_VERSION else None,
        "schema_version": schema_version,
        "work_id": work_id,
        "revision": revision,
        "name": name,
        "duration_ms": duration,
        "tracks": normalized_tracks,
    }
    if creator is not None:
        normalized_document["creator"] = creator
    return {
        "id": work_id,
        "revision": revision,
        "name": name,
        "duration_ms": duration,
        "track_count": len(normalized_tracks),
        "track_counts": counts,
        "preview_expression_id": preview_expression_id,
        "source": source,
        "modified_at": modified_at,
        "is_valid": True,
        "error": None,
        "composition": normalized_document,
        "creator": creator,
        "missing_assets": [],
    }


def invalid_work_summary(
    work_id: str,
    *,
    source: str,
    error: str,
    name: str | None = None,
    modified_at: str | None = None,
) -> dict[str, Any]:
    return {
        "id": work_id,
        "revision": 0,
        "name": name or work_id,
        "duration_ms": 0,
        "track_count": 0,
        "track_counts": {"animation": 0, "action": 0, "sound": 0},
        "preview_expression_id": None,
        "source": source,
        "modified_at": modified_at,
        "is_valid": False,
        "error": error,
        "composition": None,
        "creator": None,
        "missing_assets": [],
    }
