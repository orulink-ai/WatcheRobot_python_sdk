"""Inspect an untrusted marketplace ZIP without extracting or executing it."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO
from zipfile import BadZipFile, ZipFile

from .contract import FirmwareError, firmware_id, version

MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 64 * 1024
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", re.I)


@dataclass(frozen=True)
class Segment:
    path: str
    offset: int
    size: int
    sha256: str


@dataclass(frozen=True)
class FirmwarePackage:
    firmware_id: str
    name: str
    version: str
    chip: str
    skus: tuple[str, ...]
    hardware_revisions: tuple[str, ...]
    flash_size: int
    segments: tuple[Segment, ...]
    size: int
    sha256: str
    flash_options: tuple[tuple[str, str], ...] = ()
    description: str = ""
    author: str = ""
    notes: str = ""


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in items:
        if key in result:
            raise FirmwareError("Duplicate firmware JSON field")
        result[key] = value
    return result


def _integer(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise FirmwareError("Firmware integer is out of bounds")
    return value


def _path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise FirmwareError("Invalid firmware path")
    parts = value.split("/")
    if PurePosixPath(value).is_absolute() or any(
        not part
        or part in {".", ".."}
        or part.endswith((".", " "))
        or _RESERVED.fullmatch(part)
        or any(ord(c) < 32 or c in '\\:*?"<>|' for c in part)
        for part in parts
    ):
        raise FirmwareError("Unsafe firmware path")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 64
        or not all(
            isinstance(item, str) and 0 < len(item) <= 64 and item.strip() == item
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise FirmwareError("Invalid hardware compatibility list")
    return tuple(value)


def _digest(handle: IO[bytes]) -> str:
    digest = hashlib.sha256()
    while chunk := handle.read(64 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _flash_arguments(
    text: str, segments: tuple[Segment, ...], flash_size: int
) -> dict[str, str]:
    try:
        tokens = shlex.split(text, comments=True)
    except ValueError as exc:
        raise FirmwareError("Malformed flash arguments") from exc
    options = {
        "--flash_mode": {"dio", "qio", "dout", "qout"},
        "--flash_freq": {"40m", "80m"},
        "--flash_size": {f"{flash_size // (1024 * 1024)}MB"},
    }
    seen = set()
    actual = []
    flags = {
        "flash_mode": "dio",
        "flash_freq": "80m",
        "flash_size": f"{flash_size // (1024 * 1024)}MB",
    }
    while tokens:
        if len(tokens) < 2:
            raise FirmwareError("Incomplete flash arguments")
        key, value = tokens[:2]
        del tokens[:2]
        if key.startswith("--"):
            if key not in options or key in seen or value not in options[key]:
                raise FirmwareError("Unsupported flash option")
            seen.add(key)
            flags[key[2:]] = value
        else:
            try:
                offset = int(key, 0)
            except ValueError as exc:
                raise FirmwareError("Invalid flash offset") from exc
            actual.append((offset, _path(value)))
    if actual != [(item.offset, item.path) for item in segments]:
        raise FirmwareError("Flash arguments and manifest segments disagree")
    return flags


def inspect_package(path: Path) -> FirmwarePackage:
    """Validate all entries, hashes and allowed flash arguments before use."""
    try:
        with Path(path).open("rb") as source:
            source.seek(0, 2)
            size = source.tell()
            if not 0 < size <= MAX_PACKAGE_BYTES:
                raise FirmwareError("Firmware package exceeds size limit")
            source.seek(0)
            digest = _digest(source)
            source.seek(0)
            with ZipFile(source) as archive:
                entries = archive.infolist()
                if (
                    not 3 <= len(entries) <= 128
                    or sum(i.file_size for i in entries) > MAX_EXPANDED_BYTES
                ):
                    raise FirmwareError("Firmware archive exceeds expanded limits")
                names = set()
                files = {}
                for item in entries:
                    name = _path(item.filename)
                    mode = item.external_attr >> 16
                    if (
                        name.casefold() in names
                        or item.is_dir()
                        or item.flag_bits & 1
                        or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                    ):
                        raise FirmwareError("Duplicate or unsupported archive entry")
                    names.add(name.casefold())
                    files[name] = item
                for name in ("firmware.json", "flash_args.txt"):
                    if name not in files or files[name].file_size > MAX_METADATA_BYTES:
                        raise FirmwareError("Missing or oversized firmware metadata")
                manifest = json.loads(
                    archive.read("firmware.json").decode("utf-8"),
                    object_pairs_hook=_pairs,
                )
                required = {
                    "schema_version",
                    "id",
                    "name",
                    "version",
                    "chip",
                    "skus",
                    "hardware_revisions",
                    "flash_size",
                    "segments",
                }
                if (
                    not isinstance(manifest, dict)
                    or not required.issubset(manifest)
                    or set(manifest) - required - {"description", "author", "notes"}
                ):
                    raise FirmwareError("Invalid firmware manifest fields")
                if (
                    type(manifest["schema_version"]) is not int
                    or manifest["schema_version"] != 1
                    or manifest["chip"] != "esp32s3"
                ):
                    raise FirmwareError("Unsupported firmware schema or chip")
                identity = firmware_id(manifest["id"])
                version(manifest["version"])
                if (
                    not isinstance(manifest["name"], str)
                    or not manifest["name"].strip()
                    or len(manifest["name"]) > 128
                ):
                    raise FirmwareError("Invalid firmware name")
                capacity = _integer(
                    manifest["flash_size"], minimum=4 * 1024**2, maximum=32 * 1024**2
                )
                if capacity not in {
                    4 * 1024**2,
                    8 * 1024**2,
                    16 * 1024**2,
                    32 * 1024**2,
                }:
                    raise FirmwareError("Unsupported flash capacity")
                raw_segments = manifest["segments"]
                if (
                    not isinstance(raw_segments, list)
                    or not 1 <= len(raw_segments) <= 32
                ):
                    raise FirmwareError("Invalid flash segments")
                segments = []
                for raw in raw_segments:
                    if not isinstance(raw, dict) or set(raw) != {
                        "path",
                        "offset",
                        "size",
                        "sha256",
                    }:
                        raise FirmwareError("Invalid segment fields")
                    name = _path(raw["path"])
                    offset = _integer(raw["offset"], minimum=0, maximum=capacity - 1)
                    length = _integer(raw["size"], minimum=1, maximum=capacity - offset)
                    expected = raw["sha256"]
                    if (
                        not isinstance(expected, str)
                        or re.fullmatch(r"[0-9a-f]{64}", expected) is None
                    ):
                        raise FirmwareError("Invalid segment digest")
                    if (
                        not name.endswith(".bin")
                        or name not in files
                        or files[name].file_size != length
                    ):
                        raise FirmwareError("Missing or mismatched firmware image")
                    with archive.open(name) as image:
                        if _digest(image) != expected:
                            raise FirmwareError("Firmware image digest mismatch")
                    segments.append(Segment(name, offset, length, expected))
                ordered = sorted(segments, key=lambda item: item.offset)
                if any(
                    a.offset + a.size > b.offset for a, b in zip(ordered, ordered[1:])
                ):
                    raise FirmwareError("Overlapping flash segments")
                if len({s.path for s in segments}) != len(segments) or set(files) != {
                    "firmware.json",
                    "flash_args.txt",
                    *(s.path for s in segments),
                }:
                    raise FirmwareError("Unlisted or repeated firmware files")
                flash_options = _flash_arguments(
                    archive.read("flash_args.txt").decode("utf-8"),
                    tuple(segments),
                    capacity,
                )
                return FirmwarePackage(
                    identity,
                    manifest["name"],
                    manifest["version"],
                    "esp32s3",
                    _strings(manifest["skus"]),
                    _strings(manifest["hardware_revisions"]),
                    capacity,
                    tuple(segments),
                    size,
                    digest,
                    tuple(flash_options.items()),
                    _text(manifest.get("description", ""), 2048),
                    _text(manifest.get("author", ""), 128),
                    _text(manifest.get("notes", ""), 4000),
                )
    except (
        OSError,
        BadZipFile,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        raise FirmwareError("Unable to read firmware package") from exc


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise FirmwareError("Invalid firmware display metadata")
    return value
