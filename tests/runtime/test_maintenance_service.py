from __future__ import annotations

import json
import tarfile
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from watcherobot.runtime.daemon.maintenance.service import (
    CHUNK_BYTES,
    FLASH_BAUD_CANDIDATES,
    REPLACEMENT_TIMEOUT_SECONDS,
    _EsptoolProgress,
    _FirmwareBootProbe,
    MaintenanceError,
    MaintenanceService,
    _build_esptool_flash_command,
    _build_work_package,
    _inspect_sd_package,
    _negotiate_chunk_size,
    _parse_sd_install_status,
    _parse_flash_zip,
    _wait_for_sd_activation,
)


def test_esptool_command_uses_v5_option_names() -> None:
    command = _build_esptool_flash_command(
        ["runtime.exe", "--maintenance-esptool"],
        "COM29",
        {"flash_mode": "dio", "flash_freq": "80m", "flash_size": "16MB"},
    )

    assert "write-flash" in command
    assert "--flash-mode" in command
    assert "--flash-freq" in command
    assert "--flash-size" in command
    assert "default-reset" in command
    assert "hard-reset" in command
    assert not {"write_flash", "--flash_mode", "--flash_freq", "--flash_size"}.intersection(command)


def test_firmware_flash_prefers_fast_baud_and_keeps_safe_fallback() -> None:
    assert FLASH_BAUD_CANDIDATES == (921600, 460800)
    command = _build_esptool_flash_command(
        ["runtime.exe", "--maintenance-esptool"],
        "COM29",
        {"flash_mode": "dio", "flash_freq": "80m", "flash_size": "16MB"},
        baud=FLASH_BAUD_CANDIDATES[0],
    )
    assert command[command.index("-b") + 1] == "921600"


def test_sd_transfer_negotiates_larger_chunks_without_exceeding_host_limit() -> None:
    assert CHUNK_BYTES == 4096
    assert _negotiate_chunk_size(["WRSD/2", "OK", "BEGIN", "4096"]) == 4096
    assert _negotiate_chunk_size(["WRSD/2", "OK", "BEGIN", "2048"]) == 2048
    assert _negotiate_chunk_size(["WRSD/2", "OK", "BEGIN", "16384"]) == 4096
    assert _negotiate_chunk_size(["WRSD/2", "OK", "BEGIN", "invalid"]) == 4096
    assert _negotiate_chunk_size(["WRSD/2", "OK", "BEGIN", "0"]) == 4096


def test_slow_sd_replacement_waits_for_device_cleanup() -> None:
    assert REPLACEMENT_TIMEOUT_SECONDS >= 300


def test_sd_activation_keeps_transfer_baud_until_device_sends_done() -> None:
    class FakeProtocol:
        def __init__(self) -> None:
            self.lines = iter((
                "WRSD/2 STATUS extracting 89 正在解压资源文件。",
                "WRSD/2 STATUS verify_staging 94 正在校验资源目录。",
                "WRSD/2 DONE v0.0.5",
            ))

        def set_read_timeout(self, timeout: float) -> None:
            pass

        def read_line(self) -> str:
            return next(self.lines, "")

    updates: list[tuple[str, int, str]] = []
    protocol = FakeProtocol()

    completed = _wait_for_sd_activation(
        protocol,  # type: ignore[arg-type]
        "v0.0.5",
        lambda phase, value, line: updates.append((phase, value, line)),
        timeout_seconds=1,
    )

    assert completed[1:3] == ["DONE", "v0.0.5"]
    assert updates[0][0:2] == ("extracting", 89)
    assert updates[1][0:2] == ("verify_staging", 94)
    assert updates[-1][0:2] == ("activating", 98)


def test_sd_install_status_requires_bounded_numeric_progress() -> None:
    assert _parse_sd_install_status("WRSD/2 STATUS extracting 89 正在解压资源文件。") == (
        "extracting", 89, "正在解压资源文件。",
    )
    assert _parse_sd_install_status("WRSD/2 STATUS extracting 101 invalid") is None
    assert _parse_sd_install_status("WRSD/2 STATUS extracting invalid") is None


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
    parser = _EsptoolProgress(
        lambda phase, value, line: updates.append((phase, value, line)),
        [(0x0, 10), (0x100, 90)],
    )

    parser.emit("Writing at 0x0... (100 %)")
    parser.emit("Hash of data verified.")
    parser.emit("Writing at 0x100... (50 %)")

    assert updates[-1][1] == 56


def test_esptool_progress_never_moves_backwards_on_segment_summary_lines() -> None:
    updates: list[tuple[str, int, str]] = []
    parser = _EsptoolProgress(
        lambda phase, value, line: updates.append((phase, value, line)),
        [(0x0, 10), (0x100, 90)],
    )

    parser.emit("Writing at 0x0... (100 %)")
    parser.emit("Wrote 10 bytes at 0x0...")
    parser.emit("Hash of data verified.")
    parser.emit("Writing at 0x100... (50 %)")

    values = [value for _, value, _ in updates]
    assert values == sorted(values)


def test_esptool_progress_uses_flash_addresses_when_zip_order_differs() -> None:
    updates: list[tuple[str, int, str]] = []
    parser = _EsptoolProgress(
        lambda phase, value, line: updates.append((phase, value, line)),
        [
            (0x0, 10),
            (0x20000, 30),
            (0x8000, 10),
            (0x870000, 50),
        ],
    )

    parser.emit("Writing at 0x00000000... (100 %)")
    parser.emit("Writing at 0x00008000... (100 %)")
    parser.emit("Writing at 0x00020000... (100 %)")
    parser.emit("Writing at 0x00870000... (50 %)")

    assert updates[-1][1] == 73


def test_firmware_boot_probe_accepts_maintenance_ready() -> None:
    probe = _FirmwareBootProbe()

    assert probe.feed("I (42) MAIN: WatcheRobot starting") is None
    assert probe.feed("WRSD/2 READY none missing 0 0 ESP_ERR_NOT_FOUND") == [
        "WRSD/2", "READY", "none", "missing", "0", "0", "ESP_ERR_NOT_FOUND",
    ]


def test_firmware_boot_probe_rejects_assertion_before_ready() -> None:
    probe = _FirmwareBootProbe()

    probe.feed("I (6173) BEHAVIOR_STATE: catalog ready")
    with pytest.raises(MaintenanceError, match="assert failed"):
        probe.feed("assert failed: spi_flash_disable_interrupts_caches_and_other_cpu")


def test_firmware_boot_probe_rejects_restart_loop() -> None:
    probe = _FirmwareBootProbe()

    assert probe.feed("ESP-ROM:esp32s3-20210327") is None
    with pytest.raises(MaintenanceError, match="重复重启"):
        probe.feed("ESP-ROM:esp32s3-20210327")


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
