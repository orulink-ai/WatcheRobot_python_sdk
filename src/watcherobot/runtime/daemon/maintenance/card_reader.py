"""Transactional installation of official resources through a Windows SD-card reader."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable


VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ALLOWED_ROOTS = {"assets"}
ALLOWED_ROOT_FILES = {"official_catalog.json", "fixed_states.json", "resource_manifest.json"}
LEGACY_ROOT_ENTRIES = {"anim", "actions", "sfx", "behavior", "resource_catalog.json", "resource_manifest.json"}
SUPPORTED_FILESYSTEMS = {"FAT32"}
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 96 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 16 * 1024 * 1024
MAX_FILES = 512
SPACE_RESERVE_BYTES = 4 * 1024 * 1024
READER_TRANSACTION_NAME = "reader_transaction.json"
FIXED_STATES = {
    "boot",
    "standby",
    "listening",
    "thinking",
    "speaking",
    "processing",
    "error",
    "upgrade",
}
ASSET_LAYOUT = {
    "animation": ("anim", "anim", ".animpack", "animpack-v2"),
    "action": ("action", "actions", ".json", "firmware-action-json-v1"),
    "sound": ("sfx", "sfx", ".pcm", "pcm-s16le-24khz-mono"),
}
Progress = Callable[[str, int, str], None]


class CardReaderError(RuntimeError):
    """The selected card or resource archive is unsafe or invalid."""


@dataclass(frozen=True)
class VolumeInfo:
    id: str
    root: Path
    label: str
    filesystem: str
    free_bytes: int
    total_bytes: int
    serial_number: int
    current_version: str | None

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "root": str(self.root),
            "label": self.label,
            "filesystem": self.filesystem,
            "free_bytes": self.free_bytes,
            "total_bytes": self.total_bytes,
            "current_version": self.current_version,
        }


@dataclass(frozen=True)
class ArchivePlan:
    version: str
    file_count: int
    extracted_bytes: int
    manifest: dict[str, Any]
    manifest_sha256: str


def _installed_version(root: Path) -> str | None:
    manifest_path = root / "watche" / "official" / "current" / "resource_manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    value = document.get("bundle_version") if isinstance(document, dict) else None
    return value if isinstance(value, str) and VERSION_PATTERN.fullmatch(value) else None


def _volume_details(root: Path) -> tuple[str, str, int]:
    volume_name = ctypes.create_unicode_buffer(261)
    filesystem_name = ctypes.create_unicode_buffer(261)
    serial_number = ctypes.c_ulong(0)
    maximum_component = ctypes.c_ulong(0)
    flags = ctypes.c_ulong(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        str(root),
        volume_name,
        len(volume_name),
        ctypes.byref(serial_number),
        ctypes.byref(maximum_component),
        ctypes.byref(flags),
        filesystem_name,
        len(filesystem_name),
    )
    if not ok:
        return "", "", 0
    return volume_name.value, filesystem_name.value.upper(), int(serial_number.value)


def _inspect_volume(root: Path) -> VolumeInfo:
    label, filesystem, serial_number = _volume_details(root)
    usage = shutil.disk_usage(root)
    volume_id = f"{str(root).upper()}|{serial_number:08X}"
    return VolumeInfo(
        volume_id,
        root,
        label,
        filesystem,
        usage.free,
        usage.total,
        serial_number,
        _installed_version(root),
    )


def list_volumes() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    result: list[VolumeInfo] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = Path(f"{chr(ord('A') + index)}:\\")
        if ctypes.windll.kernel32.GetDriveTypeW(str(root)) != 2:
            continue
        try:
            result.append(_inspect_volume(root))
        except OSError:
            continue
    return [item.payload() for item in sorted(result, key=lambda item: str(item.root))]


def _resolve_volume(volume_id: str) -> VolumeInfo:
    for item in list_volumes():
        if item["id"] == volume_id:
            return VolumeInfo(
                id=str(item["id"]),
                root=Path(str(item["root"])),
                label=str(item["label"]),
                filesystem=str(item["filesystem"]),
                free_bytes=int(item["free_bytes"]),
                total_bytes=int(item["total_bytes"]),
                serial_number=int(str(item["id"]).rsplit("|", 1)[1], 16),
                current_version=item["current_version"] if isinstance(item["current_version"], str) else None,
            )
    raise CardReaderError("所选 SD 卡已移除或盘符身份发生变化，请重新检测。")


def _validate_volume(volume: VolumeInfo) -> None:
    if not volume.root.is_dir():
        raise CardReaderError("所选 SD 卡当前不可用。")
    system_drive = os.environ.get("SystemDrive", "C:").upper()
    if str(volume.root)[:2].upper() == system_drive:
        raise CardReaderError("拒绝将 Windows 系统盘作为 SD 卡。")
    if volume.filesystem.upper() not in SUPPORTED_FILESYSTEMS:
        actual = volume.filesystem or "未知"
        raise CardReaderError(f"SD 卡文件系统为 {actual}，当前设备要求 FAT32。")
    probe = volume.root / ".watche-sd-write-test.tmp"
    try:
        probe.write_bytes(b"watche")
        probe.unlink()
    except OSError as exc:
        probe.unlink(missing_ok=True)
        raise CardReaderError(f"所选 SD 卡不可写：{exc}") from exc


def _safe_member_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise CardReaderError(f"资源包包含不安全路径：{name}")
    if not (
        (len(path.parts) == 1 and path.name in ALLOWED_ROOT_FILES)
        or (len(path.parts) >= 2 and path.parts[0] in ALLOWED_ROOTS)
    ):
        raise CardReaderError(f"资源包包含不支持的路径：{name}")
    return path


def _sha256_stream(handle: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _bundle_hash(entries: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, sha256 in sorted(entries.items()):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(sha256.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_archive_json(archive: tarfile.TarFile, member: tarfile.TarInfo, label: str) -> dict[str, Any]:
    handle = archive.extractfile(member)
    if handle is None:
        raise CardReaderError(f"{label} 无法读取。")
    document = json.loads(handle.read().decode("utf-8-sig"))
    if not isinstance(document, dict):
        raise CardReaderError(f"{label} 必须是 JSON 对象。")
    return document


def _validate_catalog_contract(
    catalog: dict[str, Any],
    fixed_states: dict[str, Any],
    expected_files: dict[str, dict[str, Any]],
) -> None:
    expressions = catalog.get("expressions")
    if (
        catalog.get("schema_version") != 2
        or catalog.get("format") != "watche-official-catalog"
        or not isinstance(expressions, list)
        or not expressions
    ):
        raise CardReaderError("official_catalog.json 不是受支持的 v2 官方资源目录。")
    resource_ids: set[str] = set()
    referenced_assets: set[str] = set()
    for order, expression in enumerate(expressions):
        if not isinstance(expression, dict):
            raise CardReaderError("官方资源目录包含无效表情记录。")
        resource_id = expression.get("id")
        assets = expression.get("assets")
        if (
            not isinstance(resource_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,22}", resource_id)
            or resource_id in resource_ids
            or expression.get("order") != order
            or not isinstance(expression.get("display_name"), str)
            or not isinstance(expression.get("source_record_id"), str)
            or not isinstance(assets, dict)
            or "animation" not in assets
        ):
            raise CardReaderError("官方资源目录包含无效或重复的表情记录。")
        resource_ids.add(resource_id)
        for asset_name, asset in assets.items():
            if asset_name not in ASSET_LAYOUT or not isinstance(asset, dict):
                raise CardReaderError(f"官方资源目录包含不支持的素材：{resource_id}/{asset_name}")
            kind, directory, extension, asset_format = ASSET_LAYOUT[asset_name]
            sha256 = asset.get("sha256")
            size = asset.get("size")
            relative = f"assets/{directory}/{sha256}{extension}"
            expected = expected_files.get(relative)
            if (
                asset.get("kind") != kind
                or asset.get("format") != asset_format
                or not isinstance(sha256, str)
                or not SHA256_PATTERN.fullmatch(sha256)
                or not isinstance(size, int)
                or size <= 0
                or expected is None
                or expected["sha256"] != sha256
                or expected["size"] != size
            ):
                raise CardReaderError(f"官方资源目录素材引用无效：{resource_id}/{asset_name}")
            referenced_assets.add(relative)
    states = fixed_states.get("states")
    if (
        fixed_states.get("schema_version") != 1
        or not isinstance(states, dict)
        or set(states) != FIXED_STATES
        or any(not isinstance(value, str) or value not in resource_ids for value in states.values())
    ):
        raise CardReaderError("fixed_states.json 不完整或引用了未知表情。")
    packaged_assets = {relative for relative in expected_files if relative.startswith("assets/")}
    if referenced_assets != packaged_assets:
        raise CardReaderError("资源包包含未引用素材，或官方目录缺少素材引用。")


def inspect_archive(path: Path) -> ArchivePlan:
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise CardReaderError("SD 资源包不存在或超过 64 MB 限制。")
    try:
        with tarfile.open(path, "r:gz") as archive:
            members: dict[str, tarfile.TarInfo] = {}
            extracted_bytes = 0
            for member in archive.getmembers():
                relative = _safe_member_path(member.name).as_posix()
                if member.isdir():
                    continue
                if not member.isfile() or relative in members:
                    raise CardReaderError(f"资源包条目无效或重复：{relative}")
                if member.size <= 0 or member.size > MAX_SINGLE_FILE_BYTES:
                    raise CardReaderError(f"资源文件大小无效：{relative}")
                members[relative] = member
                extracted_bytes += member.size
            if len(members) > MAX_FILES or extracted_bytes > MAX_EXTRACTED_BYTES:
                raise CardReaderError("SD 资源包展开后的文件数量或容量超过限制。")
            if not ALLOWED_ROOT_FILES.issubset(members):
                raise CardReaderError("SD 资源包缺少资源清单或官方目录。")
            manifest_member = members["resource_manifest.json"]
            manifest_handle = archive.extractfile(manifest_member)
            if manifest_handle is None:
                raise CardReaderError("resource_manifest.json 无法读取。")
            manifest_payload = manifest_handle.read()
            manifest = json.loads(manifest_payload.decode("utf-8-sig"))
            version = manifest.get("bundle_version") if isinstance(manifest, dict) else None
            if (
                not isinstance(version, str)
                or not VERSION_PATTERN.fullmatch(version)
                or manifest.get("schema_version") != 2
                or manifest.get("layout_revision") != 2
                or manifest.get("product") != "WatcheRobot-S3"
            ):
                raise CardReaderError("SD 资源清单版本或布局无效。")
            manifest_files = manifest.get("files")
            if not isinstance(manifest_files, list):
                raise CardReaderError("SD 资源清单缺少文件列表。")
            expected: dict[str, dict[str, Any]] = {}
            for item in manifest_files:
                if not isinstance(item, dict):
                    raise CardReaderError("SD 资源清单包含无效文件记录。")
                relative_value = item.get("path")
                size = item.get("size")
                sha256 = str(item.get("sha256", "")).lower()
                if (
                    not isinstance(relative_value, str)
                    or not isinstance(size, int)
                    or size <= 0
                    or not SHA256_PATTERN.fullmatch(sha256)
                    or relative_value in expected
                ):
                    raise CardReaderError("SD 资源清单包含无效文件记录。")
                relative = relative_value
                _safe_member_path(relative)
                expected[relative] = {"size": size, "sha256": sha256}
            if set(members) - {"resource_manifest.json"} != set(expected):
                raise CardReaderError("资源包文件集合与 resource_manifest.json 不一致。")
            hashes: dict[str, str] = {}
            for relative, item in expected.items():
                member = members[relative]
                handle = archive.extractfile(member)
                if handle is None or member.size != item["size"]:
                    raise CardReaderError(f"资源文件大小不一致：{relative}")
                actual = _sha256_stream(handle)
                if actual != item["sha256"]:
                    raise CardReaderError(f"资源文件 SHA-256 不一致：{relative}")
                hashes[relative] = actual
            if not SHA256_PATTERN.fullmatch(str(manifest.get("bundle_sha256", "")).lower()):
                raise CardReaderError("SD 资源清单缺少有效的整包 SHA-256。")
            if _bundle_hash(hashes) != str(manifest["bundle_sha256"]).lower():
                raise CardReaderError("SD 资源整包 SHA-256 校验失败。")
            _validate_catalog_contract(
                _read_archive_json(archive, members["official_catalog.json"], "official_catalog.json"),
                _read_archive_json(archive, members["fixed_states.json"], "fixed_states.json"),
                expected,
            )
            return ArchivePlan(version, len(members), extracted_bytes, manifest, hashlib.sha256(manifest_payload).hexdigest())
    except (OSError, tarfile.TarError, UnicodeError, json.JSONDecodeError) as exc:
        raise CardReaderError(f"无法检查 SD 资源包：{exc}") from exc


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _target_path(watche: Path, current: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    return watche.joinpath(*parts) if parts[0] == "assets" else current.joinpath(*parts)


def _verify(watche: Path, current: Path, plan: ArchivePlan, progress: Progress) -> None:
    files = plan.manifest["files"]
    hashes: dict[str, str] = {}
    for index, item in enumerate(files, start=1):
        relative = item["path"]
        target = _target_path(watche, current, relative)
        if not target.is_file() or target.stat().st_size != item["size"]:
            raise CardReaderError(f"写后校验缺少资源：{relative}")
        with target.open("rb") as handle:
            actual = _sha256_stream(handle)
        if actual != item["sha256"]:
            raise CardReaderError(f"写后 SHA-256 校验失败：{relative}")
        hashes[relative] = actual
        progress("reader_verifying", 75 + index * 18 // max(1, len(files)), f"正在校验资源：{index}/{len(files)}")
    if _bundle_hash(hashes) != plan.manifest["bundle_sha256"]:
        raise CardReaderError("写后整包 SHA-256 校验失败。")


def _extract(path: Path, watche: Path, staging: Path, plan: ArchivePlan, progress: Progress) -> None:
    _remove(staging)
    staging.mkdir(parents=True)
    with tarfile.open(path, "r:gz") as archive:
        files = [item for item in archive.getmembers() if item.isfile()]
        for index, member in enumerate(files, start=1):
            relative = _safe_member_path(member.name).as_posix()
            source = archive.extractfile(member)
            if source is None:
                raise CardReaderError(f"资源文件无法读取：{relative}")
            target = _target_path(watche, staging, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            expected = next(
                (
                    item
                    for item in plan.manifest["files"]
                    if isinstance(item, dict) and item.get("path") == relative
                ),
                None,
            )
            if expected is not None and target.is_file() and target.stat().st_size == expected["size"]:
                with target.open("rb") as existing:
                    if _sha256_stream(existing) == expected["sha256"]:
                        progress(
                            "reader_writing",
                            25 + index * 45 // max(1, len(files)),
                            f"正在复用已有资源：{index}/{len(files)}",
                        )
                        continue
            temporary = target.with_name(target.name + ".part")
            temporary.unlink(missing_ok=True)
            with temporary.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            if expected is not None:
                with temporary.open("rb") as extracted:
                    actual = _sha256_stream(extracted)
                if temporary.stat().st_size != expected["size"] or actual != expected["sha256"]:
                    temporary.unlink(missing_ok=True)
                    raise CardReaderError(f"写入后激活前校验失败：{relative}")
            os.replace(temporary, target)
            progress("reader_writing", 25 + index * 45 // max(1, len(files)), f"正在写入资源：{index}/{len(files)}")


def _collect_work_asset_hashes(works: Path) -> set[str]:
    hashes: set[str] = set()
    if not works.is_dir():
        return hashes
    for manifest_path in works.glob("*/work.json"):
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        stack: list[Any] = [document]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                sha256 = value.get("sha256")
                if isinstance(sha256, str) and SHA256_PATTERN.fullmatch(sha256):
                    hashes.add(sha256)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return hashes


def _cleanup_unreferenced_assets(watche: Path, plan: ArchivePlan) -> None:
    keep_paths = {
        item["path"]
        for item in plan.manifest["files"]
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"].startswith("assets/")
    }
    keep_hashes = _collect_work_asset_hashes(watche / "works")
    assets = watche / "assets"
    if not assets.is_dir():
        return
    for asset in assets.rglob("*"):
        if not asset.is_file():
            continue
        relative = asset.relative_to(watche).as_posix()
        if relative not in keep_paths and asset.stem not in keep_hashes:
            asset.unlink()


def _cleanup_managed_layout(watche: Path) -> None:
    """Remove stale managed data while preserving creator works and active version markers."""

    for name in ("runtime", "runtime.next", "runtime.rollback", "staging"):
        _remove(watche / name)
    official = watche / "official"
    if official.is_dir():
        for entry in official.iterdir():
            if entry.name != "current":
                _remove(entry)
    system = watche / "system"
    if system.is_dir():
        for entry in system.iterdir():
            if entry.name not in {"layout.json", "accepted_official.json"}:
                _remove(entry)
    for entry in watche.iterdir():
        if entry.name not in {"assets", "official", "system", "works"}:
            _remove(entry)


def _required_install_bytes(watche: Path, plan: ArchivePlan) -> int:
    """Return bytes not already present in the content-addressed asset store."""

    required = 0
    for item in plan.manifest["files"]:
        relative = item["path"]
        if not relative.startswith("assets/"):
            required += item["size"]
            continue
        target = watche.joinpath(*PurePosixPath(relative).parts)
        if not target.is_file() or target.stat().st_size != item["size"]:
            required += item["size"]
            continue
        try:
            with target.open("rb") as handle:
                matches = _sha256_stream(handle) == item["sha256"]
        except OSError:
            matches = False
        if not matches:
            required += item["size"]
    return required


def _install_to_root(path: Path, root: Path, progress: Progress) -> str:
    plan = inspect_archive(path)
    watche = root / "watche"
    system = watche / "system"
    current = watche / "official" / "current"
    backup = watche / "official" / "rollback.reader"
    staging = watche / "staging" / "reader-content"
    transaction = system / READER_TRANSACTION_NAME
    progress("reader_preparing", 18, f"资源包 {plan.version} 校验通过，正在准备 SD 卡。")
    watche.mkdir(parents=True, exist_ok=True)
    system.mkdir(parents=True, exist_ok=True)
    current.parent.mkdir(parents=True, exist_ok=True)
    (watche / "works").mkdir(parents=True, exist_ok=True)
    if transaction.exists():
        if backup.exists():
            _remove(current)
            os.replace(backup, current)
        _remove(staging)
        transaction.unlink(missing_ok=True)
    _cleanup_unreferenced_assets(watche, plan)
    _cleanup_managed_layout(watche)
    system.mkdir(parents=True, exist_ok=True)
    current.parent.mkdir(parents=True, exist_ok=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    works_catalog = watche / "works" / "works_catalog.json"
    if not works_catalog.exists():
        _write_json_atomic(works_catalog, {"works": []})
    _write_json_atomic(system / "layout.json", {"layout_id": "watche-resource-layout", "layout_revision": 2})
    for name in LEGACY_ROOT_ENTRIES:
        _remove(root / name)
    required_bytes = _required_install_bytes(watche, plan)
    free_bytes = shutil.disk_usage(root).free
    if free_bytes < required_bytes + SPACE_RESERVE_BYTES:
        required_mb = (required_bytes + SPACE_RESERVE_BYTES) / (1024 * 1024)
        free_mb = free_bytes / (1024 * 1024)
        raise CardReaderError(
            f"SD 卡真实剩余空间不足：安装至少需要 {required_mb:.1f} MB，当前可用 {free_mb:.1f} MB。"
        )
    _write_json_atomic(transaction, {
        "schema_version": 1,
        "phase": "extracting",
        "version": plan.version,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    })
    _extract(path, watche, staging, plan, progress)
    _verify(watche, staging, plan, progress)
    _write_json_atomic(transaction, {"schema_version": 1, "phase": "switching", "version": plan.version})
    _remove(backup)
    had_current = current.exists()
    if had_current:
        os.replace(current, backup)
    try:
        os.replace(staging, current)
        _write_json_atomic(system / "accepted_official.json", {
            "schema_version": 2,
            "version": plan.version,
            "bundle_sha256": plan.manifest["bundle_sha256"],
            "manifest_sha256": plan.manifest_sha256,
        })
        _verify(watche, current, plan, progress)
        _cleanup_unreferenced_assets(watche, plan)
        _cleanup_managed_layout(watche)
    except Exception:
        _remove(current)
        if had_current and backup.exists():
            os.replace(backup, current)
        raise
    _remove(backup)
    transaction.unlink(missing_ok=True)
    progress("reader_completed", 99, f"SD 官方资源 {plan.version} 已写入并完成校验，用户作品已保留。")
    return plan.version


def install_package(path: Path, volume_id: str, progress: Progress) -> str:
    volume = _resolve_volume(volume_id)
    _validate_volume(volume)
    return _install_to_root(path, volume.root, progress)
