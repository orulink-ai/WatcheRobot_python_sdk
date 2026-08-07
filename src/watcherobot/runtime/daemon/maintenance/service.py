"""ESP32 firmware and SD resource maintenance over a local serial port."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import zlib
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from zipfile import BadZipFile, ZipFile

import serial
from serial.tools import list_ports

from .card_reader import (
    delete_work as delete_card_work,
    install_package as install_card_package,
    install_work_package as install_card_work_package,
    list_volumes,
    list_works as list_card_works,
    read_work as read_card_work,
)
from .releases import acquire_release_package, list_releases
from .work_assets import MAX_SOURCE_BYTES_BY_KIND
from .works import (
    MAX_WORK_BYTES,
    MAX_WORKS,
    WORK_ID_PATTERN,
    WorkDocumentError,
    PortableWorkPackage,
    build_portable_work_package,
    hydrate_creator_assets,
    invalid_work_summary,
    normalize_work_document,
    read_portable_work_package,
    validate_package_dependencies,
)

PROTOCOL_PREFIX = "WRSD/2"
DEFAULT_BAUD = 115200
TRANSFER_BAUD = 460800
FLASH_BAUD_CANDIDATES = (921600, 460800)
FLASH_BAUD = FLASH_BAUD_CANDIDATES[0]
CHUNK_BYTES = 4096
SPACE_RESERVE_BYTES = 4 * 1024 * 1024
REPLACEMENT_TIMEOUT_SECONDS = 300
FIRMWARE_READY_TIMEOUT_SECONDS = 45
SD_ACTIVATION_TIMEOUT_SECONDS = 1800
SD_ACTIVATION_STALL_TIMEOUT_SECONDS = 120
WORK_DISCOVERY_HANDSHAKE_ATTEMPTS = 4
WORK_DISCOVERY_RESPONSE_TIMEOUT_SECONDS = 0.75
WORK_ASSET_CHECK_TIMEOUT_SECONDS = 2.0
VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")
WRITING_PERCENT = re.compile(r"(?:\((\d+(?:\.\d+)?)\s*%\)|\]\s+(\d+(?:\.\d+)?)%)")
WRITING_ADDRESS = re.compile(r"Writing at 0x([0-9a-fA-F]+)")


class MaintenanceError(RuntimeError):
    """A user-visible package, port, or device maintenance failure."""


@dataclass
class MaintenanceJob:
    id: str
    kind: str
    port: str
    package_path: str
    transport: str = "serial"
    volume_id: str = ""
    source_type: str = "local"
    release_version: str = ""
    release_asset: str = ""
    status: str = "queued"
    phase: str = "queued"
    progress: int = 0
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "port": self.port,
            "package_path": self.package_path,
            "transport": self.transport,
            "volume_id": self.volume_id,
            "source_type": self.source_type,
            "release_version": self.release_version,
            "release_asset": self.release_asset,
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "logs": list(self.logs),
            "error": self.error,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }


Progress = Callable[[str, int, str], None]


def _is_watcher_esp32_port(item: Any) -> bool:
    """Return whether a serial descriptor is WatcheRobot's CH342 channel B."""

    description = str(getattr(item, "description", "") or "").upper()
    return (
        getattr(item, "vid", None) == 0x1A86
        and getattr(item, "pid", None) == 0x55D2
        and "USB-ENHANCED-SERIAL-B" in description
        and "CH342" in description
    )


class MaintenanceService:
    """Run one serial maintenance operation at a time without blocking REST."""

    def __init__(self) -> None:
        self._jobs: dict[str, MaintenanceJob] = {}
        self._lock = threading.Lock()
        self._serial_work_list_lock = threading.Lock()
        self._active_job_id: str | None = None
        self._work_requests: dict[str, tuple[dict[str, Any] | None, Path | None]] = {}

    def ports(self) -> list[dict[str, Any]]:
        result = []
        for item in list_ports.comports():
            if not _is_watcher_esp32_port(item):
                continue
            result.append({
                "device": item.device,
                "description": item.description or "",
                "hwid": item.hwid or "",
                "vid": item.vid,
                "pid": item.pid,
            })
        return sorted(result, key=lambda item: _port_sort_key(str(item["device"])))

    def validate_package(self, kind: str, package_path: str) -> dict[str, Any]:
        """Validate a local package before the desktop accepts or queues it."""

        package = Path(package_path).expanduser().resolve()
        if not package.is_file():
            raise MaintenanceError(f"选择的安装包不存在：{package}")
        if kind == "firmware":
            if package.suffix.lower() != ".zip":
                raise MaintenanceError("ESP32 固件必须上传 ZIP，且包内需要包含 flash_args.txt 和完整烧录分段。")
            _, segments = _parse_flash_zip(package)
            return {"kind": kind, "segment_count": len(segments)}
        if kind == "sd_resources":
            lowered_name = package.name.lower()
            if not (lowered_name.endswith(".tar.gz") or lowered_name.endswith(".tgz")):
                raise MaintenanceError("SD 官方资源必须上传 tar.gz，且包内需要包含有效的 resource_manifest.json。")
            inspected = _inspect_sd_package(package)
            return {"kind": kind, "version": inspected.version, "file_count": inspected.file_count}
        raise MaintenanceError(f"不支持校验此安装包类型：{kind}")

    def releases(self, kind: str) -> list[dict[str, Any]]:
        return list_releases(kind)

    def volumes(self) -> list[dict[str, Any]]:
        return list_volumes()

    def works(self, *, transport: str, port: str = "", volume_id: str = "") -> list[dict[str, Any]]:
        if transport not in {"serial", "card_reader"}:
            raise MaintenanceError("请选择设备端口或 SD 读卡器。")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs.get(self._active_job_id)
                if active is not None and active.status in {"queued", "running"}:
                    raise MaintenanceError("设备维护操作进行中，暂时无法读取作品。")
        if transport == "card_reader":
            if not volume_id:
                raise MaintenanceError("请先选择 SD 读卡器。")
            try:
                return list_card_works(volume_id)
            except Exception as exc:
                raise MaintenanceError(str(exc)) from exc
        normalized_port = port.strip()
        if not normalized_port:
            raise MaintenanceError("请先选择设备端口。")
        if not self._serial_work_list_lock.acquire(blocking=False):
            raise MaintenanceError("该设备端口正在读取 SD 作品，请等待当前读取完成。")
        try:
            return _list_serial_works(normalized_port)
        finally:
            self._serial_work_list_lock.release()

    def read_work(self, *, transport: str, work_id: str, port: str = "", volume_id: str = "") -> dict[str, Any]:
        if not WORK_ID_PATTERN.fullmatch(work_id):
            raise MaintenanceError("作品标识无效。")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs.get(self._active_job_id)
                if active is not None and active.status in {"queued", "running"}:
                    raise MaintenanceError("设备维护操作进行中，暂时无法导入作品。")
        if transport == "card_reader":
            if not volume_id:
                raise MaintenanceError("请先选择 SD 读卡器。")
            try:
                return read_card_work(volume_id, work_id)
            except Exception as exc:
                raise MaintenanceError(str(exc)) from exc
        if transport != "serial" or not port.strip():
            raise MaintenanceError("请先选择设备端口。")
        return _read_serial_work(port.strip(), work_id)

    def device_info(self, port: str) -> dict[str, Any]:
        normalized_port = port.strip()
        if not normalized_port:
            raise MaintenanceError("Select a serial port first.")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs.get(self._active_job_id)
                if active is not None and active.status in {"queued", "running"}:
                    raise MaintenanceError("A maintenance operation is currently using the device port.")
        protocol: _SerialProtocol | None = None
        try:
            protocol = _SerialProtocol(normalized_port)
            protocol.set_read_timeout(0.25)
            for _ in range(4):
                protocol.send("HELLO")
                try:
                    return _parse_ready_device_info(protocol.receive(1.25, ("READY",)))
                except MaintenanceError:
                    continue
            raise MaintenanceError("设备没有响应版本查询，当前固件版本显示为未知。")
        except serial.SerialException as exc:
            raise MaintenanceError(f"无法打开 {normalized_port} 查询设备版本：{exc}") from exc
        finally:
            if protocol is not None:
                try:
                    protocol.close()
                except serial.SerialException:
                    pass

    def start(
        self,
        kind: str,
        package_path: str,
        port: str,
        *,
        transport: str = "serial",
        volume_id: str = "",
        release_version: str = "",
        release_asset: str = "",
    ) -> dict[str, Any]:
        if kind not in {"firmware", "sd_resources"}:
            raise MaintenanceError(f"Unsupported maintenance job: {kind}")
        if transport not in {"serial", "card_reader"}:
            raise MaintenanceError(f"Unsupported maintenance transport: {transport}")
        if transport == "card_reader" and kind != "sd_resources":
            raise MaintenanceError("Only SD resources can be installed through a card reader.")
        source_type = "release" if release_version or release_asset else "local"
        if source_type == "release":
            if not release_version or not release_asset:
                raise MaintenanceError("Select both an official Release version and its install package.")
            package = None
        else:
            package = Path(package_path).expanduser().resolve()
            if not package.is_file():
                raise MaintenanceError(f"Selected package does not exist: {package}")
            self.validate_package(kind, str(package))
        normalized_port = port.strip()
        if transport == "serial" and not normalized_port:
            raise MaintenanceError("Select a serial port first.")
        if transport == "card_reader" and not volume_id:
            raise MaintenanceError("Select an SD-card reader first.")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs[self._active_job_id]
                if active.status in {"queued", "running"}:
                    raise MaintenanceError("Another firmware or resource operation is still running.")
            job = MaintenanceJob(
                id=uuid.uuid4().hex,
                kind=kind,
                port=normalized_port,
                package_path=str(package) if package is not None else "",
                transport=transport,
                volume_id=volume_id,
                source_type=source_type,
                release_version=release_version,
                release_asset=release_asset,
            )
            self._jobs[job.id] = job
            self._active_job_id = job.id
        threading.Thread(target=self._run, args=(job.id,), daemon=True).start()
        return job.payload()

    def start_work(
        self,
        composition: dict[str, Any] | None,
        package_path: str,
        port: str,
        *,
        transport: str = "serial",
        volume_id: str = "",
    ) -> dict[str, Any]:
        if transport not in {"serial", "card_reader"}:
            raise MaintenanceError("请选择设备端口或 SD 读卡器。")
        package = Path(package_path).expanduser().resolve() if package_path.strip() else None
        if package is not None and not package.is_file():
            raise MaintenanceError("选择的作品 ZIP 不存在。")
        if package is None and not isinstance(composition, dict):
            raise MaintenanceError("请选择作品 ZIP，或先在创作模式保存作品。")
        if package is not None and composition is not None:
            raise MaintenanceError("作品 ZIP 与当前作品不能同时作为烧录来源。")
        normalized_port = port.strip()
        if transport == "serial" and not normalized_port:
            raise MaintenanceError("Select a serial port first.")
        if transport == "card_reader" and not volume_id:
            raise MaintenanceError("请先选择 SD 读卡器。")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs[self._active_job_id]
                if active.status in {"queued", "running"}:
                    raise MaintenanceError("Another firmware or resource operation is still running.")
            job = MaintenanceJob(
                id=uuid.uuid4().hex,
                kind="work",
                port=normalized_port,
                package_path=str(package) if package is not None else "",
                transport=transport,
                volume_id=volume_id,
            )
            self._jobs[job.id] = job
            self._work_requests[job.id] = (composition, package)
            self._active_job_id = job.id
        threading.Thread(target=self._run, args=(job.id,), daemon=True).start()
        return job.payload()

    def export_work_package(self, composition: dict[str, Any]) -> dict[str, Any]:
        try:
            package = build_portable_work_package(composition)
        except WorkDocumentError as exc:
            raise MaintenanceError(str(exc)) from exc
        output_root = Path(tempfile.gettempdir()) / "watcherobot-work-packages"
        output_root.mkdir(parents=True, exist_ok=True)
        output = output_root / f"{package.work_id}-{uuid.uuid4().hex[:8]}.watcher-work.zip"
        output.write_bytes(package.zip_payload)
        return {
            "path": str(output),
            "file_name": f"{package.work_id}.watcher-work.zip",
            "work_id": package.work_id,
            "revision": package.revision,
        }

    def import_work_package(self, package_path: str) -> dict[str, Any]:
        try:
            package = read_portable_work_package(Path(package_path).expanduser().resolve())
            hydrated = hydrate_creator_assets(package.work, package.files)
            return normalize_work_document(
                hydrated,
                expected_id=package.work_id,
                source="local",
            )
        except (OSError, WorkDocumentError) as exc:
            raise MaintenanceError(str(exc)) from exc

    def delete_work(self, *, transport: str, work_id: str, port: str = "", volume_id: str = "") -> None:
        if not WORK_ID_PATTERN.fullmatch(work_id):
            raise MaintenanceError("作品标识无效。")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs.get(self._active_job_id)
                if active is not None and active.status in {"queued", "running"}:
                    raise MaintenanceError("设备维护操作进行中，暂时无法删除作品。")
        if transport == "card_reader":
            if not volume_id:
                raise MaintenanceError("请先选择 SD 读卡器。")
            try:
                delete_card_work(volume_id, work_id)
            except Exception as exc:
                raise MaintenanceError(str(exc)) from exc
            return
        if transport != "serial" or not port.strip():
            raise MaintenanceError("请先选择设备端口。")
        _delete_serial_work(port.strip(), work_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise MaintenanceError("Maintenance job was not found.")
            return job.payload()

    def active(self) -> dict[str, Any] | None:
        with self._lock:
            if self._active_job_id is None:
                return None
            job = self._jobs.get(self._active_job_id)
            if job is None or job.status not in {"queued", "running"}:
                return None
            return job.payload()

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            kind, port, transport, volume_id = job.kind, job.port, job.transport, job.volume_id
        self._update(
            job_id,
            status="running",
            phase="validating",
            progress=1,
            message=(
                "开始获取并校验官方 Release 安装包。"
                if job.source_type == "release"
                else "开始校验用户选择的本地文件。"
            ),
        )
        try:
            callback = lambda phase, progress, message: self._update(
                job_id, phase=phase, progress=progress, message=message
            )
            if job.source_type == "release":
                package_path = acquire_release_package(
                    kind,
                    job.release_version,
                    job.release_asset,
                    callback,
                )
                with self._lock:
                    self._jobs[job_id].package_path = str(package_path)
            else:
                package_path = Path(job.package_path)
            if kind == "firmware":
                _flash_firmware(package_path, port, callback)
            elif kind == "work":
                with self._lock:
                    composition, selected_package = self._work_requests[job_id]
                try:
                    work_package = (
                        read_portable_work_package(selected_package)
                        if selected_package is not None
                        else build_portable_work_package(composition or {})
                    )
                except WorkDocumentError as exc:
                    raise MaintenanceError(str(exc)) from exc
                if transport == "card_reader":
                    install_card_work_package(work_package, volume_id, callback)
                else:
                    _install_work(work_package, port, callback)
            elif transport == "card_reader":
                install_card_package(package_path, volume_id, callback)
            elif kind == "sd_resources":
                _install_sd_resources(package_path, port, callback)
            self._update(
                job_id,
                status="succeeded",
                phase="done",
                progress=100,
                message=(
                    "作品写入完成，可在设备或 SDK 中按作品 ID 运行。"
                    if kind == "work"
                    else "SD 卡写入完成，可安全移除读卡器并装回设备。"
                    if transport == "card_reader"
                    else "操作完成，设备已重新启动。"
                ),
            )
        except Exception as exc:
            self._update(job_id, status="failed", phase="failed", error=str(exc),
                         message=f"失败：{exc}")
        finally:
            with self._lock:
                self._work_requests.pop(job_id, None)
                if self._active_job_id == job_id:
                    self._active_job_id = None

    def _update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if status is not None:
                job.status = status
            if phase is not None:
                job.phase = phase
            if progress is not None:
                job.progress = max(job.progress, min(100, progress))
            if message:
                job.logs.append(message)
                job.logs[:] = job.logs[-2000:]
            if error is not None:
                job.error = error
            job.updated_at_ms = int(time.time() * 1000)


def _port_sort_key(device: str) -> tuple[int, str]:
    suffix = device.upper().removeprefix("COM")
    return (int(suffix), device) if suffix.isdigit() else (9999, device)


def _parse_ready_device_info(parts: list[str]) -> dict[str, Any]:
    """Parse the backward-compatible WRSD/2 READY response."""

    if len(parts) < 6 or parts[0:2] != [PROTOCOL_PREFIX, "READY"]:
        raise MaintenanceError("设备返回了无效的维护状态。")
    sd_value = parts[2]
    firmware_value = next(
        (token.removeprefix("fw=") for token in parts[6:] if token.startswith("fw=")),
        "",
    )
    capabilities = sorted({
        capability
        for token in parts[6:]
        if token.startswith("caps=")
        for capability in token.removeprefix("caps=").split(",")
        if capability
    })
    return {
        "firmware_version": firmware_value if firmware_value and firmware_value.lower() != "none" else None,
        "sd_version": sd_value if VERSION_PATTERN.fullmatch(sd_value) else None,
        "sd_state": parts[3],
        "free_bytes": int(parts[4]) if parts[4].isdigit() else 0,
        "total_bytes": int(parts[5]) if parts[5].isdigit() else 0,
        "capabilities": capabilities,
    }


@dataclass(frozen=True)
class _SerialResourceInstallPlan:
    deferred_replace: bool
    required_before_upload: int


def _serial_resource_install_plan(
    device_info: dict[str, Any],
    *,
    archive_size: int,
    expanded_size: int,
) -> _SerialResourceInstallPlan:
    free_bytes = int(device_info.get("free_bytes") or 0)
    upload_required = archive_size + SPACE_RESERVE_BYTES
    full_required = upload_required + expanded_size
    capabilities = device_info.get("capabilities")
    if isinstance(capabilities, list) and "deferred_replace" in capabilities:
        if free_bytes < upload_required:
            raise MaintenanceError(
                f"SD 卡可用空间仅 {free_bytes} 字节，接收压缩包至少需要 {upload_required} 字节。"
            )
        return _SerialResourceInstallPlan(True, upload_required)
    if free_bytes < full_required:
        raise MaintenanceError(
            "当前固件不支持先校验压缩包再清理旧资源，且 SD 卡空间不足。请先烧录兼容固件后再安装资源。"
        )
    return _SerialResourceInstallPlan(False, full_required)


def _parse_flash_zip(path: Path) -> tuple[dict[str, str], list[tuple[int, str, bytes]]]:
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            args_names = [name for name in names if Path(name.replace("\\", "/")).name.lower() == "flash_args.txt"]
            if len(args_names) != 1:
                raise MaintenanceError("固件 ZIP 必须且只能包含一个 flash_args.txt。")
            lines = [line.strip() for line in archive.read(args_names[0]).decode("utf-8").splitlines() if line.strip()]
            if len(lines) < 2:
                raise MaintenanceError("flash_args.txt 没有定义完整的烧录分段。")
            flags = {"flash_mode": "dio", "flash_freq": "80m", "flash_size": "16MB"}
            tokens = shlex.split(lines[0])
            for key in tuple(flags):
                option = f"--{key}"
                if option in tokens and tokens.index(option) + 1 < len(tokens):
                    flags[key] = tokens[tokens.index(option) + 1]
            normalized = {name.replace("\\", "/").lstrip("./"): name for name in names}
            segments: list[tuple[int, str, bytes]] = []
            for line in lines[1:]:
                pair = shlex.split(line)
                if len(pair) != 2:
                    raise MaintenanceError(f"无法解析烧录配置：{line}")
                requested = pair[1].replace("\\", "/").lstrip("./")
                member = normalized.get(requested)
                if member is None:
                    matches = [name for name in names if Path(name.replace("\\", "/")).name.lower() == Path(requested).name.lower()]
                    if len(matches) != 1:
                        raise MaintenanceError(f"固件 ZIP 缺少或无法唯一定位：{pair[1]}")
                    member = matches[0]
                segments.append((int(pair[0], 16), Path(member).name, archive.read(member)))
    except (BadZipFile, UnicodeError, ValueError, OSError) as exc:
        raise MaintenanceError(f"无效的固件 ZIP：{exc}") from exc
    basenames = {name.lower() for _, name, _ in segments}
    required = {"bootloader.bin", "partition-table.bin", "watcherobot-s3.bin"}
    if missing := sorted(required - basenames):
        raise MaintenanceError("固件 ZIP 缺少必要文件：" + ", ".join(missing))
    return flags, segments


def _flash_firmware(path: Path, port: str, progress: Progress) -> None:
    flags, segments = _parse_flash_zip(path)
    progress("validating", 5, f"固件包校验通过，共 {len(segments)} 个烧录分段。")
    with tempfile.TemporaryDirectory(prefix="watcher-flash-") as directory:
        root = Path(directory)
        entry = [sys.executable, "--maintenance-esptool"] if getattr(sys, "frozen", False) else [
            sys.executable, "-m", "watcherobot.runtime.frozen_entry", "--maintenance-esptool"
        ]
        segment_paths: list[tuple[int, Path]] = []
        for offset, name, data in segments:
            target = root / f"{offset:08x}-{name}"
            target.write_bytes(data)
            segment_paths.append((offset, target))
        ever_used_software_boot = False
        for attempt, baud in enumerate(FLASH_BAUD_CANDIDATES):
            if attempt > 0:
                # esptool may hard-reset the board while unwinding a failed
                # high-speed attempt. Give the application firmware time to
                # boot before asking it to enter the ROM downloader again.
                time.sleep(1.5)
            software_boot = _request_automatic_download_mode(port, progress)
            ever_used_software_boot = ever_used_software_boot or software_boot
            before = "no-reset" if software_boot else "default-reset"
            command = _build_esptool_flash_command(entry, port, flags, baud=baud, before=before)
            for offset, target in segment_paths:
                command.extend([hex(offset), str(target)])
            if software_boot:
                progress("connecting", 8, f"设备已自动进入下载模式，正在以 {baud} baud 连接 {port}。")
            elif before == "default-reset":
                progress("connecting", 8, f"正在以 {baud} baud 连接 {port}，设备会自动进入烧录模式。")
            else:
                progress("connecting", 8, f"正在以 {baud} baud 重新连接 {port} 的下载模式。")
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", bufsize=1)
            assert process.stdout is not None
            output: list[str] = []
            writer = _EsptoolProgress(
                progress,
                [(offset, len(data)) for offset, _, data in segments],
            )
            for line in process.stdout:
                output.append(line)
                writer.emit(line)
            if process.wait() == 0:
                break

            progress(
                "connecting",
                8,
                "烧录进程已结束，后台没有继续烧录；本次连接尚未写入 Flash。",
            )

            failure = _classify_esptool_failure("".join(output))
            if failure == "port_busy":
                raise MaintenanceError(
                    f"无法独占打开 {port}。请关闭串口监视器或其他正在使用该端口的程序后重试。"
                )
            if baud != FLASH_BAUD_CANDIDATES[-1]:
                progress(
                    "connecting",
                    8,
                    f"{baud} baud 烧录未完成，正在回退到 {FLASH_BAUD_CANDIDATES[-1]} baud 完整重试。",
                )
                continue
            if failure == "download_mode":
                if ever_used_software_boot:
                    raise MaintenanceError("设备已确认自动重启，但 ESP32-S3 下载模式没有在端口上就绪，请重新连接 USB 后重试。")
                raise MaintenanceError("当前设备固件不支持自动进入烧录模式，且 USB 串口未提供硬件自动复位能力。")
            raise MaintenanceError("固件烧录工具执行失败，请查看上方日志并重新连接设备后重试。")
    progress("restarting", 96, "固件已写入并完成哈希校验，正在重启设备。")
    _wait_for_firmware_ready(port, progress)


def _build_esptool_flash_command(
    entry: list[str], port: str, flags: dict[str, str], *, baud: int = FLASH_BAUD,
    before: str = "default-reset",
) -> list[str]:
    """Build an esptool 5-compatible command without deprecated spellings."""

    return [
        *entry,
        "--chip", "esp32s3",
        "-p", port,
        "-b", str(baud),
        "--before", before,
        "--after", "hard-reset",
        "write-flash",
        "--flash-mode", flags["flash_mode"],
        "--flash-freq", flags["flash_freq"],
        "--flash-size", flags["flash_size"],
    ]


def _classify_esptool_failure(output: str) -> str:
    """Classify failures that require different user recovery actions."""

    lowered = output.lower()
    if "no serial data received" in lowered or "wrong boot mode detected" in lowered:
        return "download_mode"
    if any(marker in lowered for marker in (
        "permissionerror",
        "access is denied",
        "拒绝访问",
        "could not open port",
        "resource busy",
    )):
        return "port_busy"
    return "other"


def _request_automatic_download_mode(port: str, progress: Progress) -> bool:
    """Ask compatible firmware to reboot directly into the ESP32-S3 ROM downloader."""

    protocol: _SerialProtocol | None = None
    try:
        protocol = _SerialProtocol(port)
        protocol.send("HELLO")
        ready = protocol.receive(1.5, ("READY",))
        capabilities = set(_parse_ready_device_info(ready).get("capabilities", []))
        if "flash_boot_v1" not in capabilities:
            return False
        progress("connecting", 7, f"正在通过 {port} 请求设备自动进入 ESP32-S3 下载模式。")
        protocol.send("FLASH_BOOT")
        response = protocol.receive(2, ("OK",))
        if len(response) < 3 or response[2] != "FLASH_BOOT":
            raise MaintenanceError("设备返回了无效的自动烧录响应。")
        protocol.close()
        protocol = None
        time.sleep(0.8)
        return True
    except serial.SerialException:
        return False
    except MaintenanceError as exc:
        if protocol is not None:
            try:
                protocol.close()
            except serial.SerialException:
                pass
            protocol = None
        if "设备拒绝操作" in str(exc) or "自动烧录响应" in str(exc):
            raise
        return False
    finally:
        if protocol is not None:
            try:
                protocol.close()
            except serial.SerialException:
                pass


class _FirmwareBootProbe:
    _CRASH_MARKERS = (
        "assert failed:",
        "guru meditation error",
        "abort() was called",
        "panic'ed",
    )

    def __init__(self) -> None:
        self._rom_boots = 0
        self._diagnostics: list[str] = []

    @property
    def diagnostics(self) -> list[str]:
        return list(self._diagnostics)

    def feed(self, line: str) -> list[str] | None:
        text = line.strip()
        if not text:
            return None
        self._diagnostics.append(text)
        self._diagnostics[:] = self._diagnostics[-80:]

        lowered = text.lower()
        if "anim manifest missing" in lowered:
            raise MaintenanceError(
                "固件已写入，但历史固件与当前 SD 资源布局不兼容：缺少 "
                "/sdcard/anim/anim_manifest.bin。请改为烧录当前兼容固件包。"
            )
        for marker in self._CRASH_MARKERS:
            if marker in lowered:
                raise MaintenanceError(f"固件已写入，但设备启动崩溃：{text}")

        if "ESP-ROM:" in text:
            self._rom_boots += 1
            if self._rom_boots >= 2:
                raise MaintenanceError("固件已写入，但设备在启动阶段重复重启。")

        protocol_marker = text.find(PROTOCOL_PREFIX + " ")
        if protocol_marker < 0:
            return None
        parts = text[protocol_marker:].split()
        if len(parts) >= 2 and parts[1] == "READY":
            return parts
        return None


class _EsptoolProgress:
    def __init__(self, progress: Progress, segments: list[tuple[int, int]]) -> None:
        self._progress = progress
        self._segments = sorted(segments)
        self._total = max(1, sum(size for _, size in self._segments))
        self._last_value = 10

    def emit(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        address_match = WRITING_ADDRESS.search(text)
        percent_match = WRITING_PERCENT.search(text)
        value = self._last_value
        if address_match and percent_match and self._segments:
            address = int(address_match.group(1), 16)
            segment_index = 0
            for index, (offset, _) in enumerate(self._segments):
                if address < offset:
                    break
                segment_index = index
            _, segment_size = self._segments[segment_index]
            completed = sum(size for _, size in self._segments[:segment_index])
            percent = float(percent_match.group(1) or percent_match.group(2))
            current = min(segment_size, int(segment_size * percent / 100))
            value = 10 + (completed + current) * 85 // self._total
        value = min(95, max(self._last_value, value))
        self._last_value = value
        self._progress("flashing", value, text)


@dataclass(frozen=True)
class _SdPackage:
    version: str
    size: int
    sha256: str
    expanded_size: int
    file_count: int
    object_count: int


def _inspect_sd_package(path: Path) -> _SdPackage:
    try:
        with tarfile.open(path, "r:gz") as archive:
            handle = archive.extractfile(archive.getmember("resource_manifest.json"))
            if handle is None:
                raise MaintenanceError("SD 包缺少 resource_manifest.json。")
            manifest = json.load(handle)
            members = [item for item in archive.getmembers() if item.isfile()]
    except (KeyError, OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        raise MaintenanceError(f"无效的 SD tar.gz：{exc}") from exc
    version = manifest.get("bundle_version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise MaintenanceError("SD 包的版本字段无效。")
    if manifest.get("schema_version") != 2 or manifest.get("layout_revision") != 2:
        raise MaintenanceError("SD 包不是当前设备支持的资源布局。")
    return _SdPackage(version, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest(),
                      sum(item.size for item in members), len(members),
                      sum(item.name.startswith("assets/") for item in members))


def _build_work_package(composition: dict[str, Any]) -> PortableWorkPackage:
    """Build one self-contained, portable work package without an official SD archive."""

    try:
        return build_portable_work_package(composition)
    except WorkDocumentError as exc:
        raise MaintenanceError(str(exc)) from exc


class _SerialProtocol:
    def __init__(self, port: str) -> None:
        self._port = port
        self._serial = serial.Serial()
        self._serial.port = port
        self._serial.baudrate = DEFAULT_BAUD
        self._serial.timeout = 1
        self._serial.write_timeout = 10
        self._serial.dtr = False
        self._serial.rts = False
        self._serial.open()
        self._transfer_baud_active = False

    def close(self) -> None:
        if not self._serial.is_open:
            return
        self._restore_default_baud()
        self._serial.close()

    def send(self, command: str) -> None:
        self._serial.write(f"{PROTOCOL_PREFIX} {command}\n".encode("ascii"))
        self._serial.flush()

    def set_read_timeout(self, timeout: float) -> None:
        self._serial.timeout = timeout

    def set_baudrate(self, baudrate: int) -> None:
        self._serial.baudrate = baudrate

    def reset_input_buffer(self) -> None:
        self._serial.reset_input_buffer()

    def read_line(self) -> str:
        return self._serial.readline().decode("utf-8", errors="ignore").strip()

    def receive(self, timeout: float, accepted: tuple[str, ...]) -> list[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self._serial.readline().decode("utf-8", errors="ignore").strip()
            marker = text.find(PROTOCOL_PREFIX + " ")
            if marker < 0:
                continue
            parts = text[marker:].split()
            if len(parts) >= 2 and parts[1] == "ERROR":
                raise MaintenanceError("设备拒绝操作：" + " ".join(parts[2:]))
            if len(parts) >= 2 and parts[1] in accepted:
                return parts
        raise MaintenanceError("等待设备响应超时，请确认已先安装支持 SD 串口维护的固件。")

    def _wait_for_ready(self, attempts: int) -> list[str] | None:
        for _ in range(attempts):
            self.send("HELLO")
            try:
                return self.receive(2, ("READY",))
            except MaintenanceError:
                continue
        return None

    def _restore_default_baud(self) -> None:
        if not self._transfer_baud_active:
            return
        try:
            self.send(f"BAUD {DEFAULT_BAUD}")
            self.receive(3, ("OK",))
        except (MaintenanceError, serial.SerialException):
            pass
        finally:
            time.sleep(0.15)
            self._serial.baudrate = DEFAULT_BAUD
            self._serial.reset_input_buffer()
            self._transfer_baud_active = False

    def _recover_default_baud(self) -> bool:
        self._serial.baudrate = TRANSFER_BAUD
        self._serial.reset_input_buffer()
        if self._wait_for_ready(2) is None:
            self._serial.baudrate = DEFAULT_BAUD
            self._serial.reset_input_buffer()
            return False
        self._transfer_baud_active = True
        self._restore_default_baud()
        return True

    def handshake(
        self,
        *,
        attempts: int = 20,
        response_timeout_seconds: float = 2.0,
    ) -> list[str]:
        for _ in range(attempts):
            self.send("HELLO")
            try:
                ready = self.receive(response_timeout_seconds, ("READY",))
                break
            except MaintenanceError:
                continue
        else:
            raise MaintenanceError("设备没有响应 SD 维护请求，请先烧录兼容固件。")
        self.send(f"BAUD {TRANSFER_BAUD}")
        self.receive(3, ("OK",))
        time.sleep(0.15)
        self._serial.baudrate = TRANSFER_BAUD
        self._serial.reset_input_buffer()
        self._transfer_baud_active = True
        self.send("HELLO")
        return self.receive(3, ("READY",))


def _receive_work_payload(
    protocol: _SerialProtocol,
    command: str,
    *,
    timeout_seconds: float = 15.0,
    max_bytes: int = MAX_WORK_BYTES,
) -> bytes:
    protocol.send(command)
    protocol.set_read_timeout(0.25)
    deadline = time.monotonic() + timeout_seconds
    expected_sequence = 0
    payload = bytearray()
    while time.monotonic() < deadline:
        text = protocol.read_line()
        marker = text.find(PROTOCOL_PREFIX + " ")
        if marker < 0:
            continue
        parts = text[marker:].split(maxsplit=3)
        if len(parts) < 2:
            continue
        if parts[1] == "ERROR":
            raise MaintenanceError("设备无法读取 SD 作品：" + " ".join(parts[2:]))
        if parts[1] == "WORK_DATA":
            if len(parts) != 4:
                raise MaintenanceError("设备返回了不完整的作品数据块。")
            try:
                sequence = int(parts[2])
                chunk = base64.b64decode(parts[3], validate=True)
            except (ValueError, TypeError) as exc:
                raise MaintenanceError("设备返回的作品数据块编码无效。") from exc
            if sequence != expected_sequence:
                raise MaintenanceError("设备返回的作品数据块顺序不连续。")
            if not chunk or len(payload) + len(chunk) > max_bytes:
                raise MaintenanceError(f"设备返回的作品数据超过 {max_bytes // 1024} KB 限制。")
            payload.extend(chunk)
            expected_sequence += 1
            continue
        if parts[1] != "WORK_DONE":
            continue
        if len(parts) != 4:
            raise MaintenanceError("设备返回的作品完成帧无效。")
        try:
            expected_size = int(parts[2])
            expected_crc = int(parts[3], 16)
        except ValueError as exc:
            raise MaintenanceError("设备返回的作品校验信息无效。") from exc
        if expected_size != len(payload) or zlib.crc32(payload) & 0xFFFFFFFF != expected_crc:
            raise MaintenanceError("设备返回的作品内容校验失败，请重试。")
        return bytes(payload)
    raise MaintenanceError("读取设备 SD 作品超时，请确认固件支持作品读取。")


def _receive_work_document(
    protocol: _SerialProtocol,
    command: str,
    *,
    timeout_seconds: float = 15.0,
    max_bytes: int = MAX_WORK_BYTES,
) -> dict[str, Any]:
    payload = _receive_work_payload(
        protocol,
        command,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaintenanceError("设备上的作品文件不是有效 JSON。") from exc
    if not isinstance(document, dict):
        raise MaintenanceError("设备上的作品文件必须是 JSON 对象。")
    return document


def _read_serial_work(port: str, work_id: str) -> dict[str, Any]:
    protocol: _SerialProtocol | None = None
    try:
        protocol = _SerialProtocol(port)
        ready = protocol.handshake()
        info = _parse_ready_device_info(ready)
        if len(ready) < 4 or ready[3] != "mounted":
            raise MaintenanceError("设备 SD 卡未挂载。")
        document = _receive_work_document(protocol, f"WORK_GET {work_id}")
        normalized = normalize_work_document(document, expected_id=work_id, source="serial")
        creator = document.get("creator")
        assets = creator.get("assets", []) if isinstance(creator, dict) else []
        if assets and "work_files_v1" not in info.get("capabilities", []):
            raise MaintenanceError("当前固件无法读取作品源素材，请先更新 ESP32 固件。")
        files: dict[str, bytes] = {}
        for asset in assets:
            relative = asset.get("sourcePath") if isinstance(asset, dict) else None
            expected_size = asset.get("size") if isinstance(asset, dict) else None
            asset_kind = asset.get("kind") if isinstance(asset, dict) else None
            if (
                not isinstance(relative, str)
                or not relative.startswith("sources/")
                or not isinstance(expected_size, int)
                or expected_size <= 0
                or asset_kind not in MAX_SOURCE_BYTES_BY_KIND
                or expected_size > MAX_SOURCE_BYTES_BY_KIND[asset_kind]
            ):
                raise MaintenanceError("设备作品包含无效的源素材路径。")
            # 460800 波特率下还包含 Base64 与逐行协议开销；超时必须随素材大小增长，
            # 否则较大的合法 GIF/音频会在数据仍持续到达时被误判为读取失败。
            timeout_seconds = max(30.0, expected_size / 40_000.0 + 30.0)
            files[relative] = _receive_work_payload(
                protocol,
                f"WORK_FILE {work_id} {relative}",
                timeout_seconds=timeout_seconds,
                max_bytes=expected_size,
            )
        hydrated = hydrate_creator_assets(document, files)
        normalized["creator"] = hydrated.get("creator")
        return normalized
    except serial.SerialException as exc:
        raise MaintenanceError(f"无法打开 {port} 导入 SD 作品：{exc}") from exc
    except WorkDocumentError as exc:
        raise MaintenanceError(str(exc)) from exc
    finally:
        if protocol is not None:
            try:
                protocol.close()
            except serial.SerialException:
                pass


def _check_serial_work_assets(protocol: _SerialProtocol, work_id: str) -> list[str]:
    protocol.send(f"WORK_CHECK {work_id}")
    protocol.set_read_timeout(0.25)
    deadline = time.monotonic() + WORK_ASSET_CHECK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        text = protocol.read_line()
        marker = text.find(PROTOCOL_PREFIX + " ")
        if marker < 0:
            continue
        parts = text[marker:].split(maxsplit=3)
        if len(parts) >= 4 and parts[1:3] == ["OK", "WORK_CHECK"] and parts[3] == work_id:
            return []
        if len(parts) >= 3 and parts[1:3] == ["ERROR", "work_assets_missing"]:
            return [parts[3] if len(parts) == 4 else "设备端素材缺失或损坏"]
    return ["设备端素材检查超时"]


def _list_serial_works(port: str) -> list[dict[str, Any]]:
    protocol: _SerialProtocol | None = None
    try:
        protocol = _SerialProtocol(port)
        ready = protocol.handshake(
            attempts=WORK_DISCOVERY_HANDSHAKE_ATTEMPTS,
            response_timeout_seconds=WORK_DISCOVERY_RESPONSE_TIMEOUT_SECONDS,
        )
        if len(ready) < 6 or ready[3] != "mounted":
            raise MaintenanceError("设备 SD 卡未挂载。")
        capabilities = set(_parse_ready_device_info(ready).get("capabilities", []))
        catalog = _receive_work_document(protocol, "WORK_LIST")
        raw_works = catalog.get("works")
        if not isinstance(raw_works, list):
            raise MaintenanceError("设备作品目录格式无效。")
        if len(raw_works) > MAX_WORKS:
            raise MaintenanceError("设备作品数量超过 64 个限制。")
        result: list[dict[str, Any]] = []
        for item in raw_works:
            work_id = item.get("id") if isinstance(item, dict) else None
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(work_id, str) or not WORK_ID_PATTERN.fullmatch(work_id):
                continue
            try:
                document = _receive_work_document(protocol, f"WORK_GET {work_id}")
                normalized = normalize_work_document(document, expected_id=work_id, source="serial")
                if "work_check_v2" in capabilities:
                    normalized["missing_assets"] = _check_serial_work_assets(protocol, work_id)
                result.append(normalized)
            except (MaintenanceError, WorkDocumentError) as exc:
                result.append(invalid_work_summary(
                    work_id,
                    source="serial",
                    name=name if isinstance(name, str) and name.strip() else None,
                    error=str(exc),
                ))
        return sorted(result, key=lambda item: (not item["is_valid"], str(item["name"]).casefold()))
    except serial.SerialException as exc:
        raise MaintenanceError(f"无法打开 {port} 读取 SD 作品：{exc}") from exc
    finally:
        if protocol is not None:
            try:
                protocol.close()
            except serial.SerialException:
                pass


def _serial_official_catalog(protocol: _SerialProtocol) -> dict[str, Any]:
    try:
        document = _receive_work_document(protocol, "WORK_RESOURCES", max_bytes=128 * 1024)
    except MaintenanceError as exc:
        raise MaintenanceError(
            "当前固件不支持作品素材校验，请先烧录支持 work_resources 的最新固件。"
        ) from exc
    if not isinstance(document.get("expressions"), list):
        raise MaintenanceError("设备 SD 官方素材目录格式无效。")
    return document


def _delete_serial_work(port: str, work_id: str) -> None:
    protocol: _SerialProtocol | None = None
    try:
        protocol = _SerialProtocol(port)
        ready = protocol.handshake()
        info = _parse_ready_device_info(ready)
        if "work_delete" not in info.get("capabilities", []):
            raise MaintenanceError("当前固件不支持通过端口删除 SD 作品，请先更新固件。")
        protocol.send(f"WORK_DELETE {work_id}")
        completed = protocol.receive(60, ("OK",))
        if len(completed) < 4 or completed[2:4] != ["WORK_DELETE", work_id]:
            raise MaintenanceError("设备返回了不匹配的作品删除结果。")
    except serial.SerialException as exc:
        raise MaintenanceError(f"无法打开 {port} 删除 SD 作品：{exc}") from exc
    finally:
        if protocol is not None:
            try:
                protocol.close()
            except serial.SerialException:
                pass


def _wait_for_firmware_ready(port: str, progress: Progress) -> list[str]:
    deadline = time.monotonic() + FIRMWARE_READY_TIMEOUT_SECONDS
    probe = _FirmwareBootProbe()
    last_serial_error: str | None = None
    progress("waiting_device", 98, f"固件写入完成，正在等待 {port} 返回 {PROTOCOL_PREFIX} READY。")

    while time.monotonic() < deadline:
        protocol: _SerialProtocol | None = None
        try:
            protocol = _SerialProtocol(port)
            protocol.set_read_timeout(0.25)
            next_hello_at = 0.0
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now >= next_hello_at:
                    protocol.send("HELLO")
                    next_hello_at = now + 1.0
                text = protocol.read_line()
                if not text:
                    continue
                progress("waiting_device", 99, f"[设备启动] {text}")
                ready = probe.feed(text)
                if ready is not None:
                    progress("device_ready", 99, f"设备维护服务已就绪：{' '.join(ready)}")
                    return ready
        except MaintenanceError:
            raise
        except serial.SerialException as exc:
            last_serial_error = str(exc)
            time.sleep(0.4)
        finally:
            if protocol is not None:
                try:
                    protocol.close()
                except serial.SerialException:
                    pass

    detail = probe.diagnostics[-1] if probe.diagnostics else last_serial_error
    suffix = f"，最后信息：{detail}" if detail else ""
    raise MaintenanceError(f"固件已写入，但设备未在 {FIRMWARE_READY_TIMEOUT_SECONDS} 秒内进入维护就绪状态{suffix}")


def _parse_sd_activation_response(text: str, expected_version: str) -> list[str] | None:
    marker = text.find(PROTOCOL_PREFIX + " ")
    if marker < 0:
        return None
    parts = text[marker:].split()
    if len(parts) < 2:
        return None
    if parts[1] == "ERROR":
        raise MaintenanceError("设备拒绝资源启用操作：" + " ".join(parts[2:]))
    if parts[1] == "DONE":
        if len(parts) > 2 and parts[2] != expected_version:
            raise MaintenanceError(f"设备启用了 {parts[2]}，预期为 {expected_version}。")
        return parts
    if (
        parts[1] == "READY"
        and len(parts) >= 7
        and parts[2] == expected_version
        and parts[3] == "mounted"
        and parts[6] == "ESP_OK"
    ):
        return parts
    return None


def _parse_sd_install_status(text: str) -> tuple[str, int, str] | None:
    """Parse a progress frame emitted by the device while COMMIT is running."""

    marker = text.find(PROTOCOL_PREFIX + " ")
    if marker < 0:
        return None
    parts = text[marker:].split(maxsplit=4)
    if len(parts) < 4 or parts[1] != "STATUS":
        return None
    try:
        progress = int(parts[3])
    except ValueError:
        return None
    if not 0 <= progress <= 100:
        return None
    detail = parts[4] if len(parts) == 5 else "设备正在处理 SD 官方资源。"
    return parts[2], progress, detail


def _negotiate_chunk_size(begin: list[str]) -> int:
    if len(begin) < 4:
        return CHUNK_BYTES
    try:
        device_limit = int(begin[3])
    except (TypeError, ValueError):
        return CHUNK_BYTES
    if device_limit <= 0:
        return CHUNK_BYTES
    return min(CHUNK_BYTES, device_limit)


def _wait_for_sd_activation(
    protocol: _SerialProtocol,
    expected_version: str,
    progress: Progress,
    *,
    timeout_seconds: float = SD_ACTIVATION_TIMEOUT_SECONDS,
    stall_timeout_seconds: float = SD_ACTIVATION_STALL_TIMEOUT_SECONDS,
) -> list[str]:
    """Wait for DONE while distinguishing a slow install from a stalled device.

    Every valid STATUS frame refreshes the stall deadline.  The independent
    total deadline still bounds a device that keeps emitting heartbeats but
    never completes.  Older firmware remains compatible because its existing
    phase STATUS frames are valid heartbeats as well.
    """

    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    stall_deadline = started_at + min(timeout_seconds, stall_timeout_seconds)
    last_phase = "installing"
    last_progress = 82
    last_detail = "设备尚未返回安装进度。"
    protocol.set_read_timeout(0.25)
    while (now := time.monotonic()) < deadline:
        if now >= stall_deadline:
            raise MaintenanceError(
                f"设备在 {stall_timeout_seconds:g} 秒内没有返回新的安装心跳；"
                f"最后阶段 {last_phase}（{last_progress}%）：{last_detail}"
            )
        line = protocol.read_line()
        status = _parse_sd_install_status(line)
        if status is not None:
            last_phase, last_progress, last_detail = status
            stall_deadline = min(deadline, time.monotonic() + stall_timeout_seconds)
            progress(*status)
            continue
        completed = _parse_sd_activation_response(line, expected_version)
        if completed is not None:
            progress("activating", 98, f"设备已确认启用 SD 官方资源 {expected_version}。")
            return completed

    raise MaintenanceError(
        f"资源已上传，但设备未在 {timeout_seconds:g} 秒内确认启用 {expected_version}；"
        f"最后阶段 {last_phase}（{last_progress}%）：{last_detail}"
    )


def _install_sd_resources(path: Path, port: str, progress: Progress) -> None:
    package = _inspect_sd_package(path)
    progress("validating", 5, f"SD 资源包 {package.version} 校验通过，SHA-256 {package.sha256}。")
    try:
        protocol = _SerialProtocol(port)
    except serial.SerialException as exc:
        raise MaintenanceError(f"无法打开串口 {port}：{exc}") from exc
    try:
        progress("connecting", 8, f"正在通过 {port} 连接设备。")
        ready = protocol.handshake()
        if len(ready) < 6 or ready[3] != "mounted":
            raise MaintenanceError("设备 SD 卡未挂载，请检查卡片并重启设备。")
        device_info = _parse_ready_device_info(ready)
        install_plan = _serial_resource_install_plan(
            device_info,
            archive_size=package.size,
            expanded_size=package.expanded_size,
        )
        if install_plan.deferred_replace:
            progress("preparing", 10, "设备将先接收并校验压缩包，再清理旧官方资源；用户作品会保留。")
            protocol.send("REPLACE")
            replaced = protocol.receive(REPLACEMENT_TIMEOUT_SECONDS, ("OK",))
            if len(replaced) < 6 or replaced[2] != "REPLACE" or replaced[5] != "ESP_OK":
                raise MaintenanceError("设备未能进入安全资源替换模式。")
            if int(replaced[3]) < install_plan.required_before_upload:
                raise MaintenanceError("SD 卡剩余空间不足，无法先接收并校验所选资源包。")
        else:
            progress("preparing", 10, "当前设备空间可同时容纳旧资源和新资源，开始安全写入。")
        protocol.send(f"BEGIN {package.version} {package.size} {package.expanded_size} "
                      f"{package.file_count} {package.object_count} {package.sha256}")
        begin = protocol.receive(10, ("OK",))
        chunk_size = _negotiate_chunk_size(begin)
        sent = 0
        last_reported_percent = -2
        with path.open("rb") as handle:
            for sequence, payload in enumerate(iter(lambda: handle.read(chunk_size), b"")):
                command = f"DATA {sequence} {zlib.crc32(payload) & 0xffffffff:08x} " + base64.b64encode(payload).decode("ascii")
                for attempt in range(3):
                    protocol.send(command)
                    try:
                        ack = protocol.receive(8, ("ACK",))
                        if int(ack[2]) != sequence:
                            raise MaintenanceError("设备返回了错误的数据块序号。")
                        break
                    except MaintenanceError:
                        if attempt == 2:
                            protocol.send("ABORT")
                            raise
                sent += len(payload)
                percent = int(sent * 100 / package.size)
                if percent == 100 or percent >= last_reported_percent + 2:
                    progress("uploading", 10 + percent * 70 // 100,
                             f"正在上传资源：{percent}%（{sent}/{package.size} 字节）")
                    last_reported_percent = percent
        progress("installing", 82, "上传完成，设备正在校验、解压并启用资源。")
        protocol.send("COMMIT")
        _wait_for_sd_activation(protocol, package.version, progress)
        progress("restarting", 98, f"SD 官方资源 {package.version} 已安装，设备正在重启。")
    finally:
        protocol.close()


def _install_work(
    package: PortableWorkPackage,
    port: str,
    progress: Progress,
) -> None:
    progress("validating", 5, f"作品 {package.name} 已校验，正在内存中生成设备作品包。")
    try:
        protocol = _SerialProtocol(port)
    except serial.SerialException as exc:
        raise MaintenanceError(f"Unable to open serial port {port}: {exc}") from exc
    try:
        progress("connecting", 8, f"正在通过 {port} 连接设备。")
        ready = protocol.handshake()
        if len(ready) < 6 or ready[3] != "mounted":
            raise MaintenanceError("Device SD card is not mounted.")
        info = _parse_ready_device_info(ready)
        capabilities = set(info.get("capabilities", []))
        required = {"work_write_v2", "work_resources"}
        if not required.issubset(capabilities):
            raise MaintenanceError("当前固件不支持 v2 作品写入与素材校验，请先更新固件。")
        # The device keeps the compressed upload and extracted incoming work
        # at the same time, then atomically replaces the previous revision.
        # Compressed size alone is not a safe estimate for AnimPack/PCM files.
        required_free = (
            len(package.payload)
            + package.expanded_size_bytes
            + SPACE_RESERVE_BYTES
        )
        free_bytes = int(info.get("free_bytes") or 0)
        if free_bytes < required_free:
            raise MaintenanceError(
                f"设备 SD 卡空间不足：安装作品至少需要 {required_free / (1024 * 1024):.1f} MB，"
                f"当前可用 {free_bytes / (1024 * 1024):.1f} MB。官方资源和其他作品不会被自动删除。"
            )
        try:
            validate_package_dependencies(package, _serial_official_catalog(protocol))
        except WorkDocumentError as exc:
            raise MaintenanceError(str(exc)) from exc
        protocol.send(
            f"WORK_BEGIN {package.work_id} {len(package.payload)} {package.sha256} {package.expanded_size_bytes}"
        )
        begin = protocol.receive(10, ("OK",))
        chunk_size = _negotiate_chunk_size(begin)
        sent = 0
        for sequence, offset in enumerate(range(0, len(package.payload), chunk_size)):
            payload = package.payload[offset:offset + chunk_size]
            command = f"DATA {sequence} {zlib.crc32(payload) & 0xffffffff:08x} " + base64.b64encode(payload).decode("ascii")
            protocol.send(command)
            ack = protocol.receive(8, ("ACK",))
            if int(ack[2]) != sequence:
                raise MaintenanceError("Device returned an unexpected work data sequence.")
            sent += len(payload)
            progress("uploading", 10 + sent * 70 // len(package.payload),
                     f"正在写入作品：{sent}/{len(package.payload)} 字节。")
        progress("installing", 82, "作品上传完成，设备正在校验并更新作品目录。")
        protocol.send("COMMIT")
        completed = protocol.receive(300, ("DONE",))
        if len(completed) < 3 or completed[2] != package.work_id:
            raise MaintenanceError("Device completed a different work installation.")
        progress("activating", 98, f"作品 {package.name} 已保存到 SD 卡。")
    finally:
        protocol.close()
