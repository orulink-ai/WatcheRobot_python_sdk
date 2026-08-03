from __future__ import annotations

import json
import tarfile
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from watcherobot.runtime.daemon.maintenance.service import (
    _EsptoolProgress,
    MaintenanceError,
    MaintenanceService,
    _build_work_package,
    _inspect_sd_package,
    _parse_flash_zip,
)


def test_flash_zip_requires_complete_layout(tmp_path: Path) -> None:
    package = tmp_path / "firmware.zip"
    with ZipFile(package, "w") as archive:
        archive.writestr("flash_args.txt", "--flash_mode dio --flash_freq 80m --flash_size 16MB\n0x0 bootloader.bin\n0x8000 partition-table.bin\n0x20000 WatcheRobot-S3.bin\n")
        archive.writestr("bootloader.bin", b"boot")
        archive.writestr("partition-table.bin", b"part")
        archive.writestr("WatcheRobot-S3.bin", b"app")

    flags, segments = _parse_flash_zip(package)

    assert flags == {"flash_mode": "dio", "flash_freq": "80m", "flash_size": "16MB"}
    assert [item[0] for item in segments] == [0, 0x8000, 0x20000]


def test_sd_package_is_inspected_without_extracting_to_disk(tmp_path: Path) -> None:
    package = tmp_path / "resources.tar.gz"
    manifest = json.dumps({"schema_version": 2, "layout_revision": 2, "bundle_version": "v0.0.2"}).encode()
    with tarfile.open(package, "w:gz") as archive:
        info = tarfile.TarInfo("resource_manifest.json")
        info.size = len(manifest)
        archive.addfile(info, BytesIO(manifest))
        asset = tarfile.TarInfo("assets/animations/demo.riv")
        asset.size = 4
        archive.addfile(asset, BytesIO(b"rive"))

    inspected = _inspect_sd_package(package)

    assert inspected.version == "v0.0.2"
    assert inspected.file_count == 2
    assert inspected.object_count == 1
    assert list(tmp_path.iterdir()) == [package]


def test_service_rejects_missing_local_package() -> None:
    service = MaintenanceService()
    with pytest.raises(MaintenanceError, match="does not exist"):
        service.start("firmware", "missing.zip", "COM29")


def test_esptool_progress_is_weighted_across_all_segments() -> None:
    updates: list[tuple[str, int, str]] = []
    parser = _EsptoolProgress(lambda phase, value, line: updates.append((phase, value, line)), [10, 90])

    parser.emit("Compressed 10 bytes to 5...")
    parser.emit("Writing at 0x0... (100 %)")
    parser.emit("Hash of data verified.")
    parser.emit("Compressed 90 bytes to 45...")
    parser.emit("Writing at 0x100... (50 %)")

    assert updates[-1][1] == 56


def test_work_package_is_built_in_memory_from_official_catalog(tmp_path: Path) -> None:
    sd_package = tmp_path / "resources.tar.gz"
    catalog = {
        "expressions": [{
            "id": "happy",
            "device": {"assets": {"animation": {
                "kind": "anim", "sha256": "a" * 64, "size": 123,
                "format": "animpack-v2",
            }}},
        }],
    }
    manifest = {"schema_version": 2, "layout_revision": 2, "bundle_version": "v0.0.2"}
    with tarfile.open(sd_package, "w:gz") as archive:
        for name, value in (("official_catalog.json", catalog), ("resource_manifest.json", manifest)):
            payload = json.dumps(value).encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, BytesIO(payload))

    package = _build_work_package({
        "name": "My Work",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 500}],
    }, sd_package)

    assert package.work_id == "my_work"
    with tarfile.open(fileobj=BytesIO(package.payload), mode="r:gz") as archive:
        assert sorted(archive.getnames()) == ["work.json", "work_manifest.json"]
        work = json.load(archive.extractfile("work.json"))
    assert work["tracks"][0]["asset"]["sha256"] == "a" * 64


def test_work_package_rejects_unknown_official_asset(tmp_path: Path) -> None:
    sd_package = tmp_path / "resources.tar.gz"
    with tarfile.open(sd_package, "w:gz") as archive:
        payload = json.dumps({"expressions": []}).encode()
        info = tarfile.TarInfo("official_catalog.json")
        info.size = len(payload)
        archive.addfile(info, BytesIO(payload))

    with pytest.raises(MaintenanceError, match="missing"):
        _build_work_package({
            "name": "Missing",
            "clips": [{"kind": "expression", "resourceId": "missing", "startMs": 0, "durationMs": 500}],
        }, sd_package)
