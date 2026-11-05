"""Pure firmware layout inspection and installation policy. No device I/O."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from .contract import FirmwareError
from .package import FirmwarePackage, inspect_package


@dataclass(frozen=True)
class Partition:
    type: int
    subtype: int
    offset: int
    size: int
    label: str
    flags: int


@dataclass(frozen=True)
class Installation:
    package: FirmwarePackage
    partitions: tuple[Partition, ...]
    mode: str
    table_offset: int | None = None


@dataclass(frozen=True)
class InstallPlan:
    mode: str
    requires_confirmation: bool
    package_sha256: str
    device_layout_sha256: str


def parse_partitions(data: bytes, capacity: int) -> tuple[Partition, ...]:
    if not data or len(data) > 4096 or len(data) % 32:
        raise FirmwareError("Invalid partition table length")
    result = []
    ended = False
    for index in range(0, len(data), 32):
        row = data[index : index + 32]
        if row == b"\xff" * 32:
            if any(value != 255 for value in data[index:]):
                raise FirmwareError("Unexpected data after partition terminator")
            ended = True
            break
        if row[:2] == b"\xeb\xeb":
            if (
                row[2:16] != b"\xff" * 14
                or row[16:] != hashlib.md5(data[:index]).digest()
            ):
                raise FirmwareError("Invalid partition table checksum")
            if any(value != 255 for value in data[index + 32 :]):
                raise FirmwareError("Unexpected data after partition checksum")
            ended = True
            break
        magic, kind, sub, offset, size, label, flags = struct.unpack("<HBBII16sI", row)
        if (
            magic != 0x50AA
            or kind == 255
            or size <= 0
            or offset % (65536 if kind == 0 else 4096)
            or size % 4096
            or offset + size > capacity
            or flags & ~3
        ):
            raise FirmwareError("Invalid partition entry")
        try:
            name = label.split(b"\0", 1)[0].decode("utf-8")
        except UnicodeError as exc:
            raise FirmwareError("Invalid partition label") from exc
        result.append(Partition(kind, sub, offset, size, name, flags))
    if not result or not ended:
        raise FirmwareError("Incomplete partition table")
    ordered = sorted(result, key=lambda item: item.offset)
    if len({p.label for p in result}) != len(result) or any(
        a.offset + a.size > b.offset for a, b in zip(ordered, ordered[1:])
    ):
        raise FirmwareError("Overlapping or duplicate partitions")
    return tuple(result)


def _verify_image(data: bytes) -> None:
    """Verify the ESP image framing/checksum and optional appended SHA-256."""
    if (
        len(data) < 24
        or data[0] != 0xE9
        or not 1 <= data[1] <= 16
        or struct.unpack_from("<H", data, 12)[0] != 9
        or data[23] not in (0, 1)
    ):
        raise FirmwareError("Invalid ESP32-S3 image header")
    position = 24
    checksum = 0xEF
    for _ in range(data[1]):
        if position + 8 > len(data):
            raise FirmwareError("Truncated firmware image segment header")
        _, length = struct.unpack_from("<II", data, position)
        position += 8
        end = position + length
        if length % 4 or end > len(data):
            raise FirmwareError("Invalid firmware image segment length")
        for value in data[position:end]:
            checksum ^= value
        position = end
    checksum_at = (position // 16 + 1) * 16 - 1
    if checksum_at >= len(data) or data[checksum_at] != checksum:
        raise FirmwareError("Invalid firmware image checksum")
    if data[23]:
        digest_at = checksum_at + 1
        if (
            len(data) < digest_at + 32
            or data[digest_at : digest_at + 32]
            != hashlib.sha256(data[:digest_at]).digest()
        ):
            raise FirmwareError("Invalid firmware image SHA-256")


def inspect_installation(path: Path, *, require_layout: bool = False) -> Installation:
    package = inspect_package(path)
    with ZipFile(path) as archive:
        reference = package.partition_table
        if not reference:
            # Legacy complete packages can carry their table as a write segment.
            candidates = [
                s for s in package.segments if archive.read(s.path)[:2] == b"\xaa\x50"
            ]
            reference = candidates[0].path if len(candidates) == 1 else ""
        if not reference:
            if require_layout:
                raise FirmwareError(
                    "Missing reference partition table; rebuild the installation package"
                )
            return Installation(package, (), "unverified")
        partitions = parse_partitions(archive.read(reference), package.flash_size)
        table_segments = [s for s in package.segments if s.path == reference]
        mode = "full" if table_segments else "application"
        table_offset = table_segments[0].offset if table_segments else None
        if table_offset is not None and (
            table_offset < 0x1000
            or table_offset % 4096
            or any(p.offset < table_offset + 4096 for p in partitions)
        ):
            raise FirmwareError("Invalid partition table write location")
        boot = [s for s in package.segments if s.offset == 0]
        if mode == "full" and not boot:
            raise FirmwareError(
                "Full installation requires a bootloader image at address zero"
            )
        applications = 0
        for segment in package.segments:
            if segment in table_segments or segment in boot:
                continue
            partition = next(
                (
                    p
                    for p in partitions
                    if p.offset <= segment.offset
                    and segment.offset + ((segment.size + 4095) // 4096) * 4096
                    <= p.offset + p.size
                ),
                None,
            )
            if partition is None or segment.offset != partition.offset:
                raise FirmwareError(
                    "Image write address does not match partition layout"
                )
            if partition.type == 0:
                image = archive.read(segment.path)
                _verify_image(image)
                applications += 1
            elif mode == "application":
                raise FirmwareError(
                    "Application update cannot overwrite data partitions"
                )
        if not applications or (mode == "application" and boot):
            raise FirmwareError("Incomplete application installation")
        if boot:
            image = archive.read(boot[0].path)
            _verify_image(image)
        return Installation(package, partitions, mode, table_offset)


def plan_installation(
    info: Installation, device_table: bytes, flash_size: int
) -> InstallPlan:
    if info.mode == "unverified":
        raise FirmwareError(
            "Package has no verifiable partition layout; contact the publisher"
        )
    if flash_size < info.package.flash_size:
        raise FirmwareError(
            "Device flash capacity is smaller than the package requires"
        )
    if info.mode == "application":
        current = parse_partitions(device_table, flash_size)
        # Compare semantic entries; padding and table checksums need not match.
        if sorted(current, key=lambda p: p.offset) != sorted(
            info.partitions, key=lambda p: p.offset
        ):
            raise FirmwareError(
                "Device partition layout differs; a complete installation package is required"
            )
    return InstallPlan(
        info.mode,
        info.mode == "full",
        info.package.sha256,
        hashlib.sha256(device_table).hexdigest(),
    )
