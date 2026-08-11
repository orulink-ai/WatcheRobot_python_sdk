from __future__ import annotations

import json
import tarfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from watcherobot.runtime.daemon.maintenance import service as maintenance_service
from watcherobot.runtime.daemon.maintenance.service import (
    CHUNK_BYTES,
    FLASH_BAUD_CANDIDATES,
    REPLACEMENT_TIMEOUT_SECONDS,
    _EsptoolProgress,
    _FirmwareBootProbe,
    MaintenanceError,
    MaintenanceService,
    _build_esptool_flash_command,
    _classify_esptool_failure,
    _flash_firmware,
    _request_automatic_download_mode,
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


def test_esptool_command_can_preserve_manual_download_mode() -> None:
    command = _build_esptool_flash_command(
        ["runtime.exe", "--maintenance-esptool"],
        "COM29",
        {"flash_mode": "dio", "flash_freq": "80m", "flash_size": "16MB"},
        baud=460800,
        before="no-reset",
    )

    assert command[command.index("--before") + 1] == "no-reset"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("Failed to connect to ESP32-S3: No serial data received.", "download_mode"),
        ("Wrong boot mode detected (0x13)!", "download_mode"),
        ("could not open port 'COM29': PermissionError(13, '拒绝访问。')", "port_busy"),
        ("A fatal error occurred: Packet content transfer stopped", "other"),
    ],
)
def test_esptool_failure_is_classified_for_actionable_recovery(output: str, expected: str) -> None:
    assert _classify_esptool_failure(output) == expected


def test_firmware_requests_device_bootloader_before_esptool(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "firmware.zip"
    with ZipFile(package, "w") as archive:
        archive.writestr(
            "flash_args.txt",
            "--flash_mode dio --flash_freq 80m --flash_size 16MB\n"
            "0x0 bootloader.bin\n0x8000 partition-table.bin\n0x20000 WatcheRobot-S3.bin\n",
        )
        archive.writestr("bootloader.bin", b"boot")
        archive.writestr("partition-table.bin", b"part")
        archive.writestr("WatcheRobot-S3.bin", b"app")

    commands: list[list[str]] = []
    class FakeProcess:
        def __init__(self, command: list[str], **_kwargs) -> None:
            commands.append(command)
            self.stdout = iter(["Hash of data verified.\n"])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(maintenance_service.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(maintenance_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(maintenance_service, "_wait_for_firmware_ready", lambda *_args: None)
    monkeypatch.setattr(maintenance_service, "_request_automatic_download_mode", lambda *_args: True)
    updates: list[str] = []

    _flash_firmware(package, "COM29", lambda _phase, _value, line: updates.append(line))

    assert commands[0][commands[0].index("--before") + 1] == "no-reset"
    assert commands[0][commands[0].index("-b") + 1] == "921600"
    assert any("自动进入下载模式" in line for line in updates)


def test_firmware_retry_reenters_bootloader_before_safe_baud(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = tmp_path / "firmware.zip"
    with ZipFile(package, "w") as archive:
        archive.writestr(
            "flash_args.txt",
            "--flash_mode dio --flash_freq 80m --flash_size 16MB\n"
            "0x0 bootloader.bin\n0x8000 partition-table.bin\n0x20000 WatcheRobot-S3.bin\n",
        )
        archive.writestr("bootloader.bin", b"boot")
        archive.writestr("partition-table.bin", b"part")
        archive.writestr("WatcheRobot-S3.bin", b"app")

    commands: list[list[str]] = []
    return_codes = iter((2, 0))

    class FakeProcess:
        def __init__(self, command: list[str], **_kwargs) -> None:
            commands.append(command)
            self.return_code = next(return_codes)
            self.stdout = iter(
                [
                    "esptool failed: Unable to verify flash chip connection "
                    "(No more data to read from the serial port.)\n"
                ]
                if self.return_code
                else ["Hash of data verified.\n"]
            )

        def wait(self) -> int:
            return self.return_code

    boot_requests: list[str] = []

    def request_bootloader(port: str, _progress) -> bool:
        boot_requests.append(port)
        return True

    monkeypatch.setattr(maintenance_service.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(maintenance_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(maintenance_service, "_wait_for_firmware_ready", lambda *_args: None)
    monkeypatch.setattr(maintenance_service, "_request_automatic_download_mode", request_bootloader)

    _flash_firmware(package, "COM29", lambda *_args: None)

    assert boot_requests == ["COM29", "COM29"]
    assert [command[command.index("-b") + 1] for command in commands] == [
        "921600",
        "460800",
    ]
    assert all(command[command.index("--before") + 1] == "no-reset" for command in commands)


def test_firmware_flash_reports_download_mode_instead_of_port_occupancy(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "firmware.zip"
    with ZipFile(package, "w") as archive:
        archive.writestr(
            "flash_args.txt",
            "--flash_mode dio --flash_freq 80m --flash_size 16MB\n"
            "0x0 bootloader.bin\n0x8000 partition-table.bin\n0x20000 WatcheRobot-S3.bin\n",
        )
        archive.writestr("bootloader.bin", b"boot")
        archive.writestr("partition-table.bin", b"part")
        archive.writestr("WatcheRobot-S3.bin", b"app")

    class FakeProcess:
        def __init__(self, _command: list[str], **_kwargs) -> None:
            self.stdout = iter(["Failed to connect to ESP32-S3: No serial data received.\n"])

        def wait(self) -> int:
            return 2

    monkeypatch.setattr(maintenance_service.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(maintenance_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(maintenance_service, "_request_automatic_download_mode", lambda *_args: False)

    updates: list[str] = []
    with pytest.raises(MaintenanceError, match="自动进入烧录模式"):
        _flash_firmware(package, "COM29", lambda _phase, _value, line: updates.append(line))

    assert any("后台没有继续烧录" in line for line in updates)
    assert any("尚未写入 Flash" in line for line in updates)


def test_automatic_download_mode_uses_selected_dynamic_port(monkeypatch) -> None:
    instances = []

    class FakeProtocol:
        def __init__(self, port: str) -> None:
            self.port = port
            self.commands: list[str] = []
            instances.append(self)

        def send(self, command: str) -> None:
            self.commands.append(command)

        def receive(self, _timeout: float, accepted: tuple[str, ...]) -> list[str]:
            if accepted == ("READY",):
                return [
                    "WRSD/2", "READY", "v0.0.8", "mounted", "1024", "2048", "ESP_OK",
                    "fw=V3.1", "caps=deferred_replace,flash_boot_v1",
                ]
            return ["WRSD/2", "OK", "FLASH_BOOT"]

        def close(self) -> None:
            pass

    monkeypatch.setattr(maintenance_service, "_SerialProtocol", FakeProtocol)

    assert _request_automatic_download_mode("COM47", lambda *_args: None) is True
    assert instances[0].port == "COM47"
    assert instances[0].commands == ["HELLO", "FLASH_BOOT"]


def test_automatic_download_mode_falls_back_for_legacy_firmware(monkeypatch) -> None:
    class FakeProtocol:
        def __init__(self, _port: str) -> None:
            self.commands: list[str] = []

        def send(self, command: str) -> None:
            self.commands.append(command)

        def receive(self, _timeout: float, _accepted: tuple[str, ...]) -> list[str]:
            return ["WRSD/2", "READY", "v0.0.8", "mounted", "1024", "2048", "ESP_OK", "fw=v0.3.2"]

        def close(self) -> None:
            pass

    monkeypatch.setattr(maintenance_service, "_SerialProtocol", FakeProtocol)

    assert _request_automatic_download_mode("COM8", lambda *_args: None) is False


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


def test_sd_activation_reports_last_heartbeat_when_device_stalls(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProtocol:
        def set_read_timeout(self, timeout: float) -> None:
            assert timeout == 0.25

        def read_line(self) -> str:
            return "WRSD/2 STATUS extract_write 87 file=demo.animpack;files=2/92;bytes=1048576;total=72312857;bps=65536"

    moments = iter((0.0, 0.0, 1.0, 3.0))
    monkeypatch.setattr(maintenance_service.time, "monotonic", lambda: next(moments))

    with pytest.raises(MaintenanceError, match=r"extract_write.*87%.*demo\.animpack"):
        _wait_for_sd_activation(
            FakeProtocol(),  # type: ignore[arg-type]
            "v0.0.8",
            lambda *_args: None,
            timeout_seconds=10,
            stall_timeout_seconds=2,
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
    with pytest.raises(MaintenanceError):
        service.validate_package("firmware", "missing.zip")


def test_maintenance_ports_only_expose_watcher_ch342_channel_b(monkeypatch) -> None:
    monkeypatch.setattr(maintenance_service.list_ports, "comports", lambda: [
        SimpleNamespace(device="COM1", description="Communication Port", hwid="ACPI", vid=None, pid=None),
        SimpleNamespace(device="COM28", description="USB-Enhanced-SERIAL-A CH342", hwid="USB", vid=0x1A86, pid=0x55D2),
        SimpleNamespace(device="COM29", description="USB-Enhanced-SERIAL-B CH342", hwid="USB", vid=0x1A86, pid=0x55D2),
        SimpleNamespace(device="COM31", description="USB Serial Device", hwid="USB", vid=0x1234, pid=0x5678),
    ])

    assert [item["device"] for item in MaintenanceService().ports()] == ["COM29"]


def test_local_package_is_validated_before_job_is_queued(tmp_path: Path) -> None:
    invalid_firmware = tmp_path / "not-firmware.zip"
    invalid_firmware.write_bytes(b"not a zip")
    invalid_resources = tmp_path / "not-resources.tar.gz"
    invalid_resources.write_bytes(b"not a tar gzip")
    service = MaintenanceService()

    with pytest.raises(MaintenanceError, match="无效的固件 ZIP"):
        service.validate_package("firmware", str(invalid_firmware))
    with pytest.raises(MaintenanceError, match="无效的 SD tar.gz"):
        service.validate_package("sd_resources", str(invalid_resources))

    assert service.active() is None


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


def test_firmware_boot_probe_reports_legacy_animation_layout_failure() -> None:
    probe = _FirmwareBootProbe()

    with pytest.raises(MaintenanceError, match="固件已写入.*历史固件.*SD 资源布局不兼容"):
        probe.feed("E (2227) MAIN: Fatal boot error: Anim manifest missing")


def test_work_package_uses_stable_work_id_and_embeds_editable_source() -> None:
    package = _build_work_package({
        "kind": "watcher.creator-composition",
        "version": 2,
        "workId": "w_9f4d2a1c",
        "revision": 3,
        "name": "中文作品",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 500}],
    })

    assert package.work_id == "w_9f4d2a1c"
    with tarfile.open(fileobj=BytesIO(package.payload), mode="r:gz") as archive:
        assert sorted(archive.getnames()) == ["work.json", "work_manifest.json"]
        work = json.load(archive.extractfile("work.json"))
        manifest = json.load(archive.extractfile("work_manifest.json"))
    assert work["schema_version"] == 2
    assert work["revision"] == 3
    assert work["creator"]["workId"] == "w_9f4d2a1c"
    assert work["tracks"][0]["asset"] == {
        "source": "official",
        "resource_id": "happy",
        "kind": "anim",
    }
    assert work["tracks"][0]["clip_id"] == "clip-1"
    assert manifest["dependencies"] == [{"resource_id": "happy", "kinds": ["animation"]}]


def test_work_package_bundles_creator_action_tracks_without_requiring_official_action() -> None:
    package = _build_work_package({
        "kind": "watcher.creator-composition",
        "version": 2,
        "workId": "portable_action",
        "revision": 2,
        "name": "Portable action",
        "clips": [{
            "id": "custom-action-1",
            "kind": "action",
            "resourceId": "custom-action-1720000000",
            "label": "自定义动作",
            "startMs": 0,
            "durationMs": 1000,
            "actionTracks": {
                "xDeg": [
                    {"id": "x0", "axis": "xDeg", "timeMs": 0, "frameNumber": 0, "angleDeg": 90, "source": "draft"},
                    {"id": "x1", "axis": "xDeg", "timeMs": 1000, "frameNumber": 50, "angleDeg": 120, "source": "draft"},
                ],
                "yDeg": [
                    {"id": "y0", "axis": "yDeg", "timeMs": 0, "frameNumber": 0, "angleDeg": 100, "source": "draft"},
                ],
            },
        }],
    })

    with tarfile.open(fileobj=BytesIO(package.payload), mode="r:gz") as archive:
        names = sorted(archive.getnames())
        work = json.load(archive.extractfile("work.json"))
        manifest = json.load(archive.extractfile("work_manifest.json"))
        catalog = json.load(archive.extractfile("resource_catalog.json"))
        action_name = next(name for name in names if name.startswith("actions/"))
        action = json.load(archive.extractfile(action_name))

    assert names == sorted(["work.json", "work_manifest.json", "resource_catalog.json", action_name])
    assert work["tracks"][0]["asset"]["source"] == "work"
    assert work["tracks"][0]["asset"]["resource_id"].startswith("wa_")
    assert manifest["dependencies"] == []
    assert manifest["bundled_assets"][0]["path"] == action_name
    assert catalog["expressions"][0]["assets"]["action"]["path"] == action_name
    assert action["animated_objects"][0]["object_name"] == "body_x"
    assert action["animated_objects"][1]["object_name"] == "head_y"


def test_work_package_rejects_invalid_explicit_work_id() -> None:
    with pytest.raises(MaintenanceError, match="work_id"):
        _build_work_package({
            "workId": "../../escape",
            "name": "Missing",
            "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 500}],
        })


def test_legacy_compositions_with_chinese_names_do_not_share_one_work_id() -> None:
    first = _build_work_package({
        "name": "早安作品",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 500}],
    })
    second = _build_work_package({
        "name": "晚安作品",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 500}],
    })

    assert first.work_id.startswith("work_")
    assert second.work_id.startswith("work_")
    assert first.work_id != second.work_id
