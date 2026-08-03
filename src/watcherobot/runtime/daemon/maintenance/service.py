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

PROTOCOL_PREFIX = "WRSD/2"
DEFAULT_BAUD = 115200
TRANSFER_BAUD = 460800
FLASH_BAUD = 460800
CHUNK_BYTES = 2048
SPACE_RESERVE_BYTES = 4 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")
WRITING_PERCENT = re.compile(r"(?:\((\d+(?:\.\d+)?)\s*%\)|\]\s+(\d+(?:\.\d+)?)%)")


class MaintenanceError(RuntimeError):
    """A user-visible package, port, or device maintenance failure."""


@dataclass
class MaintenanceJob:
    id: str
    kind: str
    port: str
    package_path: str
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
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "logs": list(self.logs),
            "error": self.error,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }


Progress = Callable[[str, int, str], None]


class MaintenanceService:
    """Run one serial maintenance operation at a time without blocking REST."""

    def __init__(self) -> None:
        self._jobs: dict[str, MaintenanceJob] = {}
        self._lock = threading.Lock()
        self._active_job_id: str | None = None
        self._work_requests: dict[str, tuple[dict[str, Any], Path]] = {}

    def ports(self) -> list[dict[str, Any]]:
        result = []
        for item in list_ports.comports():
            result.append({
                "device": item.device,
                "description": item.description or "",
                "hwid": item.hwid or "",
                "vid": item.vid,
                "pid": item.pid,
            })
        return sorted(result, key=lambda item: _port_sort_key(str(item["device"])))

    def start(self, kind: str, package_path: str, port: str) -> dict[str, Any]:
        if kind not in {"firmware", "sd_resources"}:
            raise MaintenanceError(f"Unsupported maintenance job: {kind}")
        package = Path(package_path).expanduser().resolve()
        if not package.is_file():
            raise MaintenanceError(f"Selected package does not exist: {package}")
        normalized_port = port.strip()
        if not normalized_port:
            raise MaintenanceError("Select a serial port first.")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs[self._active_job_id]
                if active.status in {"queued", "running"}:
                    raise MaintenanceError("Another firmware or resource operation is still running.")
            job = MaintenanceJob(
                id=uuid.uuid4().hex,
                kind=kind,
                port=normalized_port,
                package_path=str(package),
            )
            self._jobs[job.id] = job
            self._active_job_id = job.id
        threading.Thread(target=self._run, args=(job.id,), daemon=True).start()
        return job.payload()

    def start_work(
        self,
        composition: dict[str, Any],
        sd_package_path: str,
        port: str,
    ) -> dict[str, Any]:
        package = Path(sd_package_path).expanduser().resolve()
        if not package.is_file():
            raise MaintenanceError("Select the matching SD resource package before burning a work.")
        normalized_port = port.strip()
        if not normalized_port:
            raise MaintenanceError("Select a serial port first.")
        with self._lock:
            if self._active_job_id is not None:
                active = self._jobs[self._active_job_id]
                if active.status in {"queued", "running"}:
                    raise MaintenanceError("Another firmware or resource operation is still running.")
            job = MaintenanceJob(
                id=uuid.uuid4().hex,
                kind="work",
                port=normalized_port,
                package_path=str(package),
            )
            self._jobs[job.id] = job
            self._work_requests[job.id] = (composition, package)
            self._active_job_id = job.id
        threading.Thread(target=self._run, args=(job.id,), daemon=True).start()
        return job.payload()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise MaintenanceError("Maintenance job was not found.")
            return job.payload()

    def _run(self, job_id: str) -> None:
        self._update(job_id, status="running", phase="validating", progress=1,
                     message="开始校验用户选择的本地文件。")
        with self._lock:
            job = self._jobs[job_id]
            kind, package_path, port = job.kind, Path(job.package_path), job.port
        try:
            callback = lambda phase, progress, message: self._update(
                job_id, phase=phase, progress=progress, message=message
            )
            if kind == "firmware":
                _flash_firmware(package_path, port, callback)
            elif kind == "sd_resources":
                _install_sd_resources(package_path, port, callback)
            else:
                with self._lock:
                    composition, sd_package = self._work_requests[job_id]
                _install_work(composition, sd_package, port, callback)
            self._update(job_id, status="succeeded", phase="done", progress=100,
                         message="操作完成，设备已重新启动。")
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
        command = [*entry, "--chip", "esp32s3", "-p", port,
                   "-b", str(FLASH_BAUD), "--before", "default_reset", "--after", "hard_reset",
                   "write_flash", "--flash_mode", flags["flash_mode"], "--flash_freq",
                   flags["flash_freq"], "--flash_size", flags["flash_size"]]
        for offset, name, data in segments:
            target = root / f"{offset:08x}-{name}"
            target.write_bytes(data)
            command.extend([hex(offset), str(target)])
        progress("connecting", 8, f"正在连接 {port}，设备会自动进入烧录模式。")
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1)
        assert process.stdout is not None
        writer = _EsptoolProgress(progress, [len(data) for _, _, data in segments])
        for line in process.stdout:
            writer.emit(line)
        if process.wait() != 0:
            raise MaintenanceError("固件烧录工具执行失败，请检查端口是否被占用并重新连接设备。")
    progress("restarting", 98, "固件已写入并完成哈希校验，正在重启设备。")


class _EsptoolProgress:
    def __init__(self, progress: Progress, segment_sizes: list[int]) -> None:
        self._progress = progress
        self._sizes = segment_sizes
        self._index = -1
        self._completed = 0
        self._total = max(1, sum(segment_sizes))

    def emit(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        if text.startswith("Compressed ") or text.startswith("Writing at ") and self._index < 0:
            self._index = min(self._index + 1, len(self._sizes) - 1)
        match = WRITING_PERCENT.search(text)
        if match and self._index >= 0:
            percent = float(match.group(1) or match.group(2))
            current = int(self._sizes[self._index] * percent / 100)
            value = 10 + (self._completed + current) * 85 // self._total
        else:
            value = 10 + self._completed * 85 // self._total
        self._progress("flashing", value, text)
        if text == "Hash of data verified." and 0 <= self._index < len(self._sizes):
            self._completed += self._sizes[self._index]


@dataclass(frozen=True)
class _SdPackage:
    version: str
    size: int
    sha256: str
    expanded_size: int
    file_count: int
    object_count: int


@dataclass(frozen=True)
class _WorkPackage:
    work_id: str
    name: str
    payload: bytes
    sha256: str


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


def _safe_work_id(name: str) -> str:
    value = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
    if not value or not value[0].isalpha():
        value = f"work_{value}"
    return value[:23].rstrip("_") or "work"


def _read_official_catalog(path: Path) -> dict[str, Any]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            handle = archive.extractfile(archive.getmember("official_catalog.json"))
            if handle is None:
                raise MaintenanceError("SD package is missing official_catalog.json.")
            value = json.load(handle)
    except (KeyError, OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        raise MaintenanceError(f"Invalid SD package catalog: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("expressions"), list):
        raise MaintenanceError("SD package catalog has no expression list.")
    return value


def _build_work_package(composition: dict[str, Any], sd_package: Path) -> _WorkPackage:
    name = str(composition.get("name") or "Untitled work").strip()[:64]
    clips = composition.get("clips")
    if not isinstance(clips, list) or not clips:
        raise MaintenanceError("The current work has no timeline clips.")
    entries = {
        item.get("id"): item
        for item in _read_official_catalog(sd_package).get("expressions", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    tracks: list[dict[str, Any]] = []
    kind_map = {
        "expression": ("animation", "anim", "animation"),
        "action": ("action", "action", "action"),
        "sound": ("sound", "sfx", "sound"),
    }
    for clip in clips:
        if not isinstance(clip, dict) or clip.get("kind") == "light":
            continue
        clip_kind = clip.get("kind")
        resource_id = clip.get("resourceId")
        if clip_kind not in kind_map or not isinstance(resource_id, str):
            raise MaintenanceError("The work contains an unsupported timeline clip.")
        catalog_key, asset_kind, track_type = kind_map[clip_kind]
        entry = entries.get(resource_id)
        assets = {}
        if isinstance(entry, dict):
            device = entry.get("device")
            if isinstance(device, dict) and isinstance(device.get("assets"), dict):
                assets = device["assets"]
            elif isinstance(entry.get("assets"), dict):
                assets = entry["assets"]
        asset = assets.get(catalog_key) if isinstance(assets, dict) else None
        if not isinstance(asset, dict):
            raise MaintenanceError(f"Official asset {resource_id} is missing from the selected SD package.")
        start_ms = int(clip.get("startMs", 0))
        duration_ms = max(1, int(clip.get("durationMs", 1)))
        tracks.append({
            "type": track_type,
            "start_ms": max(0, start_ms),
            "duration_ms": duration_ms,
            "asset": {
                "source": "official",
                "resource_id": resource_id,
                "kind": asset_kind,
                "sha256": asset.get("sha256"),
                "size": asset.get("size"),
                "format": asset.get("format"),
            },
        })
    if not tracks:
        raise MaintenanceError("The work has no animation, action, or sound clips to burn.")
    work_id = _safe_work_id(name)
    work = {
        "schema_version": 1,
        "work_id": work_id,
        "name": name,
        "duration_ms": max(item["start_ms"] + item["duration_ms"] for item in tracks),
        "tracks": tracks,
    }
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = json.dumps(work, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        for filename in ("work.json", "work_manifest.json"):
            info = tarfile.TarInfo(filename)
            info.size = len(payload)
            info.mtime = 0
            archive.addfile(info, BytesIO(payload))
    data = buffer.getvalue()
    return _WorkPackage(work_id, name, data, hashlib.sha256(data).hexdigest())


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

    def close(self) -> None:
        self._serial.close()

    def send(self, command: str) -> None:
        self._serial.write(f"{PROTOCOL_PREFIX} {command}\n".encode("ascii"))
        self._serial.flush()

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

    def handshake(self) -> list[str]:
        for _ in range(10):
            self.send("HELLO")
            try:
                ready = self.receive(2, ("READY",))
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
        self.send("HELLO")
        return self.receive(3, ("READY",))


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
        progress("clearing", 10, "正在清理设备中原有的官方资源。")
        protocol.send("REPLACE")
        replaced = protocol.receive(30, ("OK",))
        required = package.size + package.expanded_size + SPACE_RESERVE_BYTES
        if len(replaced) < 6 or replaced[2] != "REPLACE" or replaced[5] != "ESP_OK":
            raise MaintenanceError("设备未能进入资源替换模式。")
        if int(replaced[3]) < required:
            raise MaintenanceError("SD 卡剩余空间不足，无法安装所选资源包。")
        protocol.send(f"BEGIN {package.version} {package.size} {package.expanded_size} "
                      f"{package.file_count} {package.object_count} {package.sha256}")
        begin = protocol.receive(10, ("OK",))
        chunk_size = min(CHUNK_BYTES, int(begin[3])) if len(begin) >= 4 else CHUNK_BYTES
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
        completed = protocol.receive(900, ("DONE",))
        if len(completed) > 2 and completed[2] != package.version:
            raise MaintenanceError(f"设备启用了 {completed[2]}，预期为 {package.version}。")
        progress("restarting", 98, f"SD 官方资源 {package.version} 已安装，设备正在重启。")
    finally:
        protocol.close()


def _install_work(
    composition: dict[str, Any],
    sd_package: Path,
    port: str,
    progress: Progress,
) -> None:
    package = _build_work_package(composition, sd_package)
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
        protocol.send(f"WORK_BEGIN {package.work_id} {len(package.payload)} {package.sha256}")
        begin = protocol.receive(10, ("OK",))
        chunk_size = min(CHUNK_BYTES, int(begin[3])) if len(begin) >= 4 else CHUNK_BYTES
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
