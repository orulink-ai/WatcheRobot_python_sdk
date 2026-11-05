"""Prepare bounded immutable marketplace packages from ESP-IDF output."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any
from zipfile import ZipFile, ZipInfo, ZIP_STORED, BadZipFile

from .contract import FirmwareError
from .installation import inspect_installation
from .paths import filesystem_path
from .package import MAX_PACKAGE_BYTES, MAX_EXPANDED_BYTES, _path, _pairs


def _json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 65536:
        raise FirmwareError("Build metadata exceeds size limit")
    value = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise FirmwareError("Build metadata must be an object")
    return value


def _file(root: Path, name: str) -> Path:
    safe = _path(name)
    candidate = root / safe
    # Only links inside the selected root are untrusted references. System
    # ancestors such as macOS /var -> /private/var are valid filesystem roots.
    for current in (candidate, *candidate.parents):
        if current == root:
            break
        if current.is_symlink():
            raise FirmwareError("Build references cannot use symbolic links")
    if (
        not candidate.resolve().is_relative_to(root.resolve())
        or not candidate.is_file()
    ):
        raise FirmwareError(
            "Missing build file or reference outside the build directory"
        )
    return candidate


def _build(root: Path, output: Path) -> None:
    if (root / "flasher_args.json").is_file():
        args = _json(_file(root, "flasher_args.json"))
    else:
        raw_args = _file(root, "flash_args.txt")
        if raw_args.stat().st_size > 65536:
            raise FirmwareError("Flash arguments exceed size limit")
        tokens = shlex.split(raw_args.read_text(encoding="utf-8"), comments=True)
        settings = {}
        images = {}
        while tokens:
            if len(tokens) < 2:
                raise FirmwareError("Incomplete flash arguments")
            key, value = tokens[:2]
            del tokens[:2]
            if key.startswith("--"):
                if (
                    key not in ("--flash_size", "--flash_mode", "--flash_freq")
                    or key[2:] in settings
                ):
                    raise FirmwareError("Unsupported flash option")
                settings[key[2:]] = value
            else:
                address = int(key, 0)
                if str(address) in images:
                    raise FirmwareError("Duplicate flash address")
                images[str(address)] = value
        args = {
            "extra_esptool_args": {"chip": "esp32s3"},
            "flash_settings": settings,
            "flash_files": images,
        }
    if (root / "project_description.json").is_file():
        project = _json(_file(root, "project_description.json"))
    else:
        # ESP application descriptor is embedded after the image/segment headers.
        project = {}
        for value in args.get("flash_files", {}).values():
            path = _file(root, value)
            with path.open("rb") as stream:
                header = stream.read(112)
            if header[32:36] == bytes.fromhex("3254cdab"):
                project = {
                    "project_name": header[80:112].split(b"\0", 1)[0].decode("utf-8"),
                    "project_version": header[48:80].split(b"\0", 1)[0].decode("utf-8"),
                }
                break
        if not project:
            raise FirmwareError("Missing project description or application descriptor")
    if args.get("extra_esptool_args", {}).get("chip") != "esp32s3":
        raise FirmwareError("Only ESP32-S3 builds are supported")
    flash = args.get("flash_settings", {})
    capacity = flash.get("flash_size", "")
    if capacity not in ("4MB", "8MB", "16MB", "32MB"):
        raise FirmwareError("Build must specify a concrete supported flash capacity")
    raw = args.get("flash_files")
    if not isinstance(raw, dict) or not 1 <= len(raw) <= 32:
        raise FirmwareError("Missing generated flash file mapping")
    files: dict[str, bytes] = {}
    segments: list[dict[str, Any]] = []
    tables = []
    total = 0
    for key, value in raw.items():
        path = _file(root, value)
        length = path.stat().st_size
        total += length
        if total > MAX_EXPANDED_BYTES:
            raise FirmwareError("Build images exceed expanded limit")
        data = path.read_bytes()
        offset = int(key, 0)
        name = f"images/{offset:08x}.bin"
        files[name] = data
        segments.append(
            {
                "path": name,
                "offset": offset,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        if data[:2] == b"\xaa\x50":
            tables.append(name)
    if len(tables) != 1:
        raise FirmwareError("Build must contain exactly one partition table")
    segments.sort(key=lambda s: s["offset"])
    name = project.get("project_name", "")
    identity = re.sub("[^a-z0-9._-]", "-", str(name).lower()).strip("-")[:55]
    if not identity:
        raise FirmwareError("Missing project name")
    manifest = {
        "schema_version": 2,
        "id": "local." + identity,
        "name": name,
        "version": project.get("project_version", ""),
        "chip": "esp32s3",
        "skus": ["unknown"],
        "hardware_revisions": ["unknown"],
        "flash_size": int(capacity[:-2]) * 1024**2,
        "partition_table": tables[0],
        "segments": segments,
    }
    # Authors can supply display identity without manually specifying any address.
    if (root / "firmware.json").is_file():
        metadata = _json(_file(root, "firmware.json"))
        for field in (
            "id",
            "name",
            "version",
            "skus",
            "hardware_revisions",
            "description",
            "author",
            "notes",
        ):
            if field in metadata:
                manifest[field] = metadata[field]
    text = f"--flash_size {capacity} --flash_mode {flash.get('flash_mode','dio')} --flash_freq {flash.get('flash_freq','80m')}\n"
    text += "\n".join(f"{s['offset']:#x} {s['path']}" for s in segments)
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        payloads = {
            "firmware.json": json.dumps(
                manifest, ensure_ascii=True, sort_keys=True
            ).encode("utf-8"),
            "flash_args.txt": text.encode("utf-8"),
            **files,
        }
        for name in sorted(payloads):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, payloads[name])


def _extract_build(source: Path, root: Path) -> None:
    with ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 512 or sum(p.file_size for p in entries) > MAX_EXPANDED_BYTES:
            raise FirmwareError("Build ZIP exceeds expanded limits")
        seen = set()
        for item in entries:
            name = _path(item.filename.rstrip("/") if item.is_dir() else item.filename)
            mode = stat.S_IFMT(item.external_attr >> 16)
            if (
                name.casefold() in seen
                or item.flag_bits & 1
                or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
            ):
                raise FirmwareError("Duplicate or unsupported build ZIP entry")
            seen.add(name.casefold())
            dest = root / name
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(archive.read(item))


def prepare_package(source: Path, cache_root: Path) -> dict[str, Any]:
    """No credentials or device required. Publication rechecks this snapshot."""
    source = filesystem_path(source)
    cache_root = filesystem_path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix="prepare-", dir=cache_root
        ) as temporary:
            root = Path(temporary)
            output = root / "prepared.zip"
            if source.is_dir():
                _build(source, output)
            elif source.is_file() and source.stat().st_size <= MAX_PACKAGE_BYTES:
                with ZipFile(source) as archive:
                    prepared = (
                        "firmware.json" in archive.namelist()
                        and "flash_args.txt" in archive.namelist()
                    )
                if prepared:
                    output.write_bytes(source.read_bytes())
                else:
                    build = root / "build"
                    build.mkdir()
                    _extract_build(source, build)
                    _build(build, output)
            else:
                raise FirmwareError(
                    "Select a build directory or a bounded firmware ZIP"
                )
            info = inspect_installation(output, require_layout=True)
            destination = cache_root / (info.package.sha256 + ".zip")
            os.replace(output, destination)
            return {
                "package_path": str(destination),
                "mode": info.mode,
                "partitions": [asdict(p) for p in info.partitions],
                **asdict(info.package),
            }
    except (OSError, ValueError, TypeError, KeyError, BadZipFile) as exc:
        if isinstance(exc, FirmwareError):
            raise
        raise FirmwareError(f"Unable to prepare build: {exc}") from exc
