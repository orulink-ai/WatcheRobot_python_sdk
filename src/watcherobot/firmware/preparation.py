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
from .installation import Partition, inspect_installation, parse_partitions
from .paths import filesystem_path
from .package import (
    FirmwarePackage,
    MAX_PACKAGE_BYTES,
    MAX_EXPANDED_BYTES,
    MAX_METADATA_BYTES,
    _path,
    _pairs,
)


def _read_bounded(path: Path, limit: int, message: str) -> bytes:
    # A build may replace/grow a file after stat. Bound the actual read, not
    # merely the size observed before opening the stream.
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise FirmwareError(message)
    return data


def _json(path: Path) -> dict[str, Any]:
    data = _read_bounded(path, MAX_METADATA_BYTES, "Build metadata exceeds size limit")
    value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs)
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


def _build(root: Path, output: Path, overrides: dict[str, str] | None = None) -> None:
    metadata = {}
    if (root / "firmware.json").is_file():
        metadata = _json(_file(root, "firmware.json"))
        if "schema_version" in metadata or "segments" in metadata:
            raise FirmwareError(
                "Firmware package metadata cannot be used as build display metadata"
            )
    if (root / "flasher_args.json").is_file():
        args = _json(_file(root, "flasher_args.json"))
    else:
        raw_args = _file(
            root, "flash_args" if (root / "flash_args").is_file() else "flash_args.txt"
        )
        tokens = shlex.split(
            _read_bounded(
                raw_args, MAX_METADATA_BYTES, "Flash arguments exceed size limit"
            ).decode("utf-8"),
            comments=True,
        )
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
    for field in ("extra_esptool_args", "flash_settings", "flash_files"):
        if not isinstance(args.get(field), dict):
            raise FirmwareError(f"Build field {field} must be an object")
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
        if (
            not project
            and not (overrides or {}).get("name")
            and not metadata.get("name")
        ):
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
        data = _read_bounded(
            path, MAX_EXPANDED_BYTES - total, "Build images exceed expanded limit"
        )
        total += len(data)
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
            try:
                parse_partitions(data, int(capacity[:-2]) * 1024**2)
            except FirmwareError:
                pass
            else:
                tables.append(name)
    if len(tables) != 1:
        raise FirmwareError("Build must contain exactly one partition table")
    segments.sort(key=lambda s: s["offset"])
    name = project.get("project_name", "")
    if not isinstance(name, str) or not name.strip():
        name = (overrides or {}).get("name", metadata.get("name", ""))
    if not isinstance(name, str) or not name.strip():
        raise FirmwareError("Missing project name")
    identity = re.sub("[^a-z0-9._-]", "-", name.lower()).strip("-")[:55]
    if not identity:
        # Unicode display names do not need an ASCII spelling. Keep the ID
        # deterministic across directory/ZIP preparation and cache retries.
        identity = "project-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
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
    if metadata:
        for field in (
            "id",
            "name",
            "version",
            "skus",
            "hardware_revisions",
            "description",
            "inference_model",
            "icon_data_url",
            "author",
            "notes",
        ):
            if field in metadata:
                manifest[field] = metadata[field]
    manifest.update(overrides or {})
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


DISPLAY_FIELDS = frozenset({"id", "name", "version", "description", "author", "notes", "inference_model", "icon_data_url"})


def _display_metadata(value: dict[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if (
        not isinstance(value, dict)
        or set(value) - DISPLAY_FIELDS
        or any(not isinstance(item, str) for item in value.values())
    ):
        raise FirmwareError("Invalid display metadata fields or values")
    return dict(value)


def _applications(
    package: FirmwarePackage, partitions: tuple[Partition, ...]
) -> list[dict[str, Any]]:
    return [
        {
            "label": partition.label,
            "path": segment.path,
            "offset": segment.offset,
            "size": segment.size,
        }
        for segment in package.segments
        for partition in partitions
        if partition.type == 0 and segment.offset == partition.offset
    ]


def _rewrite_package(
    output: Path, metadata: dict[str, str], mode: str | None, application: str | None
) -> None:
    from .package import inspect_package

    package = inspect_package(output)
    with ZipFile(output) as archive:
        manifest = json.loads(archive.read("firmware.json"))
        payloads = {item.filename: archive.read(item) for item in archive.infolist()}
    reference = package.partition_table
    if not reference:
        info = inspect_installation(output, require_layout=True)
        reference = next(
            s.path for s in package.segments if s.offset == info.table_offset
        )
    partitions = parse_partitions(payloads[reference], package.flash_size)
    if mode == "full":
        if inspect_installation(output, require_layout=True).mode != "full":
            raise FirmwareError(
                "Cannot prepare full installation without bootloader and table writes"
            )
    if mode == "application":
        applications = _applications(package, partitions)
        selected = [
            item
            for item in applications
            if application is None or item["label"] == application
        ]
        if len(selected) != 1:
            raise FirmwareError("Select exactly one application partition label")
        selected_path = selected[0]["path"]
        manifest["schema_version"] = 2
        manifest["partition_table"] = reference
        manifest["segments"] = [
            s for s in manifest["segments"] if s["path"] == selected_path
        ]
        payloads = {
            key: value
            for key, value in payloads.items()
            if key in ("firmware.json", "flash_args.txt", reference, selected_path)
        }
        options = " ".join(f"--{key} {value}" for key, value in package.flash_options)
        payloads["flash_args.txt"] = (
            options + "\n" + f"{selected[0]['offset']:#x} {selected_path}\n"
        ).encode()
    manifest.update(metadata)
    payloads["firmware.json"] = json.dumps(
        manifest, ensure_ascii=True, sort_keys=True
    ).encode()
    replacement = output.with_name("edited.zip")
    with ZipFile(replacement, "w", compression=ZIP_STORED) as archive:
        for name in sorted(payloads):
            entry = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, payloads[name])
    os.replace(replacement, output)


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


def prepare_package(
    source: Path,
    cache_root: Path,
    *,
    metadata: dict[str, str] | None = None,
    mode: str | None = None,
    application: str | None = None,
) -> dict[str, Any]:
    """No credentials or device required. Publication rechecks this snapshot."""
    overrides = _display_metadata(metadata)
    if mode not in (None, "full", "application"):
        raise FirmwareError("Invalid preparation mode")
    if application is not None and (
        mode != "application" or not isinstance(application, str) or not application
    ):
        raise FirmwareError("An application label requires application mode")
    source = filesystem_path(source)
    if source.is_dir() and (source / "watcherobot.project.json").exists():
        from watcherobot.project.model import load_project

        project = load_project(source)
        if project.firmware_build_directory is None:
            raise FirmwareError("Firmware project requires a compiled build directory")
        source = project.firmware_build_directory
        overrides = {**(project.firmware_metadata or {}), **overrides}
    cache_root = filesystem_path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix="prepare-", dir=cache_root
        ) as temporary:
            root = Path(temporary)
            output = root / "prepared.zip"
            if source.is_dir():
                _build(source, output, overrides)
            elif source.is_file() and source.stat().st_size <= MAX_PACKAGE_BYTES:
                # Classification and extraction observe one private ZIP, even
                # when the selected build output is replaced concurrently.
                snapshot = root / "source.zip"
                snapshot.write_bytes(
                    _read_bounded(
                        source, MAX_PACKAGE_BYTES, "Firmware package exceeds size limit"
                    )
                )
                with ZipFile(snapshot) as archive:
                    prepared = "firmware.json" in archive.namelist()
                    if prepared:
                        if (
                            archive.getinfo("firmware.json").file_size
                            > MAX_METADATA_BYTES
                        ):
                            raise FirmwareError("Firmware metadata exceeds size limit")
                        package_metadata = json.loads(
                            archive.read("firmware.json").decode("utf-8-sig"),
                            object_pairs_hook=_pairs,
                        )
                        if not isinstance(package_metadata, dict):
                            raise FirmwareError("Firmware metadata must be an object")
                        # IDF builds may carry optional display metadata too.
                        # A package-format marker keeps malformed packages on the
                        # strict inspection path rather than rebuilding them.
                        prepared = (
                            "schema_version" in package_metadata
                            or "segments" in package_metadata
                        )
                if prepared:
                    inspect_installation(snapshot, require_layout=True)
                    os.replace(snapshot, output)
                else:
                    build = root / "build"
                    build.mkdir()
                    _extract_build(snapshot, build)
                    roots = {
                        item.parent
                        for pattern in (
                            "flasher_args.json",
                            "flash_args.txt",
                            "flash_args",
                        )
                        for item in build.rglob(pattern)
                        if item.is_file()
                    }
                    # IDF may include metadata for the bootloader sub-build.
                    # Only independent outer builds are ambiguous.
                    roots = {
                        item
                        for item in roots
                        if not any(parent in roots for parent in item.parents)
                    }
                    if len(roots) != 1:
                        raise FirmwareError(
                            "ZIP must contain exactly one build directory"
                        )
                    selected = roots.pop()
                    metadata_path = selected / "firmware.json"
                    if metadata_path.is_file():
                        metadata = _json(metadata_path)
                        if "schema_version" in metadata or "segments" in metadata:
                            raise FirmwareError(
                                "Firmware package metadata must be at ZIP root"
                            )
                    _build(selected, output, overrides)
            else:
                raise FirmwareError(
                    "Select a build directory or a bounded firmware ZIP"
                )
            if overrides or mode is not None:
                _rewrite_package(output, overrides, mode, application)
            info = inspect_installation(output, require_layout=True)
            applications = _applications(info.package, info.partitions)
            destination = cache_root / (info.package.sha256 + ".zip")
            os.replace(output, destination)
            return {
                "package_path": str(destination),
                "mode": info.mode,
                "partitions": [asdict(p) for p in info.partitions],
                "applications": applications,
                "metadata": {
                    "id": info.package.firmware_id,
                    **{
                        key: getattr(info.package, key)
                        for key in DISPLAY_FIELDS
                        if key != "id"
                    },
                },
                **asdict(info.package),
            }
    except (OSError, ValueError, TypeError, KeyError, BadZipFile) as exc:
        if isinstance(exc, FirmwareError):
            raise
        raise FirmwareError(f"Unable to prepare build: {exc}") from exc
