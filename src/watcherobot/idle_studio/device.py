"""USB maintenance adapter. No Desktop/Application business routing."""
from __future__ import annotations

import threading
import base64
import json
import zlib
import re
import time
from collections import OrderedDict
from typing import Any

from serial.tools import list_ports
from .model import SaveSession, validate_profile
from .preview import animation_gif
from .transfer import decode_block
from watcherobot.runtime.daemon.maintenance.service import (
    _SerialProtocol, _receive_work_document, _receive_work_payload, _parse_ready_device_info,
    _negotiate_chunk_size, _wait_for_sd_activation, SPACE_RESERVE_BYTES,
    MaintenanceError,
)


class DeviceReader:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self._progress = {}
        self._preview_cache = OrderedDict()
        self._cache_lock = threading.Lock()

    def preview_progress(self, request_id: str) -> dict:
        with self._progress_lock:
            return dict(self._progress.get(request_id, {'phase': 'unknown', 'received': 0, 'total': 0}))

    def _set_progress(self, request_id, phase, received=0, total=0):
        if not request_id:
            return
        with self._progress_lock:
            if request_id not in self._progress and len(self._progress) >= 32:
                del self._progress[next(iter(self._progress))]
            self._progress[request_id] = {'phase': phase, 'received': received, 'total': total}

    def ports(self) -> list[dict[str, str]]:
        return [{"port": p.device, "label": p.description} for p in list_ports.comports()]

    def preview(self, port: str, resource: str, device_id: str | None = None,
                request_id: str | None = None) -> bytes:
        if request_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', request_id):
            raise ValueError('预览请求标识无效')
        if not isinstance(resource, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,23}', resource):
            raise ValueError('素材标识无效')
        if port not in {p['port'] for p in self.ports()}:
            raise ValueError('设备已断开，请重新连接')
        if not self._lock.acquire(blocking=False):
            raise ValueError('设备操作进行中，请稍候')
        protocol = None
        self._set_progress(request_id, 'connecting')
        try:
            protocol = _SerialProtocol(port)
            protocol.send('HELLO')
            info = _parse_ready_device_info(protocol.receive(3, ('READY',)))
            if 'resource_read_v1' not in info['capabilities'] or info['sd_state'] != 'mounted':
                raise ValueError('当前固件不支持读取动画画面，请升级配套固件')
            if device_id:
                if 'joy_idle_v1' not in info['capabilities']:
                    raise ValueError('设备已变化，请重新连接')
                identity = _receive_work_document(protocol, 'IDLE_GET')
                if identity.get('device_id') != device_id:
                    raise ValueError('设备已变化，请重新连接')
            metadata = _receive_work_document(protocol, f'RESOURCE_INFO {resource}')
            size = metadata.get('size')
            if type(size) is not int or not 28 <= size <= 16 * 1024 * 1024:
                raise ValueError('设备动画大小无效')
            fingerprint = metadata.get('crc32')
            cache_key = (device_id, resource, size, fingerprint) if device_id and isinstance(fingerprint, str) and re.fullmatch(r'[0-9a-f]{8}', fingerprint) else None
            with self._cache_lock:
                if cache_key in self._preview_cache:
                    result = self._preview_cache[cache_key]
                    self._preview_cache.move_to_end(cache_key)
                    self._set_progress(request_id, 'complete', size, size)
                    return result
            payload = bytearray()
            compressed = 'resource_deflate_v1' in info['capabilities']
            self._set_progress(request_id, 'reading', 0, size)
            while len(payload) < size:
                count = min(32768 if compressed else 4096, size - len(payload))
                command = 'RESOURCE_DEFLATE' if compressed else 'RESOURCE_READ'
                block = _receive_work_payload(protocol, f'{command} {resource} {len(payload)} {count}',
                                              timeout_seconds=15, max_bytes=count + 1024 if compressed else count)
                if compressed:
                    block = decode_block(block, count)
                if len(block) != count:
                    raise ValueError('动画读取不完整，请重试')
                payload.extend(block)
                self._set_progress(request_id, 'reading', len(payload), size)
            if cache_key and f'{zlib.crc32(payload):08x}' != fingerprint:
                raise ValueError('动画素材读取期间发生变化，请重试')
        except Exception:
            self._set_progress(request_id, 'failed')
            raise
        finally:
            try:
                if protocol is not None:
                    try:
                        protocol.send('CLOSE')
                    finally:
                        protocol.close()
            finally:
                self._lock.release()
        self._set_progress(request_id, 'decoding', size, size)
        try:
            result = animation_gif(bytes(payload))
        except Exception:
            self._set_progress(request_id, 'failed', size, size)
            raise
        self._set_progress(request_id, 'complete', size, size)
        if cache_key and len(result) <= 32 * 1024 * 1024:
            with self._cache_lock:
                self._preview_cache[cache_key] = result
                self._preview_cache.move_to_end(cache_key)
                while sum(map(len, self._preview_cache.values())) > 32 * 1024 * 1024 or len(self._preview_cache) > 64:
                    self._preview_cache.popitem(last=False)
        return result

    def save(self, port: str, session: SaveSession, value: dict[str, Any]) -> dict[str, Any]:
        profile = validate_profile(value)
        payload = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > 12288:
            raise ValueError("配置内容过大")
        if port not in {p["port"] for p in self.ports()}:
            raise ValueError("设备端口已断开，请重新连接")
        if not self._lock.acquire(blocking=False):
            raise ValueError("设备操作进行中，请稍候")
        protocol = None
        try:
            protocol = _SerialProtocol(port)
            protocol.send("HELLO")
            info = _parse_ready_device_info(protocol.receive(3, ("READY",)))
            if "joy_idle_v1" not in info["capabilities"] or info["sd_state"] != "mounted":
                raise ValueError("当前设备不支持行为保存或存储卡未就绪")
            if profile["schema"] == 3 and "joy_idle_pools_v1" not in info["capabilities"]:
                raise ValueError("当前固件不支持分组概率，请升级配套固件后保存")
            before = _receive_work_document(protocol, "IDLE_GET")
            session.check(before.get("device_id", ""), before.get("revision"))
            def acknowledged(command: str, expected: list[str]) -> None:
                protocol.send(command)
                if protocol.receive(10, ("OK",))[2:] != expected:
                    raise ValueError("设备确认帧不匹配，请重新读取")
            acknowledged(f"IDLE_BEGIN {session.device_id} {session.revision} {len(payload)} {zlib.crc32(payload):08x}", ["IDLE_BEGIN"])
            for sequence, offset in enumerate(range(0, len(payload), 512)):
                encoded = base64.b64encode(payload[offset:offset + 512]).decode("ascii")
                acknowledged(f"IDLE_DATA {sequence} {encoded}", ["IDLE_DATA", str(sequence)])
            acknowledged("IDLE_COMMIT", ["IDLE_COMMIT"])
            after = _receive_work_document(protocol, "IDLE_GET")
            SaveSession(session.device_id, session.revision + 1).check(after.get("device_id", ""), after.get("revision"))
            if after.get("profile") != profile:
                raise ValueError("保存后的回读结果不一致，请重新读取设备")
            return after
        finally:
            try:
                if protocol is not None:
                    try:
                        protocol.send("CLOSE")
                    finally:
                        protocol.close()
            finally:
                self._lock.release()

    def import_expression(self, port, session, package, resource, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            raise ValueError("导入请求标识无效")
        if port not in {p["port"] for p in self.ports()}:
            raise ValueError("设备已断开，请重新连接")
        if not self._lock.acquire(blocking=False):
            raise ValueError("设备操作进行中，请稍候")
        protocol = None
        self._set_progress(request_id, "connecting")
        try:
            protocol = _SerialProtocol(port)
            protocol.send("HELLO")
            info = _parse_ready_device_info(protocol.receive(3, ("READY",)))
            if info["sd_state"] != "mounted" or not {"joy_idle_v1", "idle_catalog_v1", "work_write_v2"}.issubset(info["capabilities"]):
                raise ValueError("请升级配套固件后导入表情")
            before = _receive_work_document(protocol, "IDLE_GET")
            session.check(before.get("device_id", ""), before.get("revision"))
            catalog = _receive_work_document(protocol, "IDLE_RESOURCES", timeout_seconds=60, max_bytes=512 * 1024)
            if any(item.get("id") == resource for item in catalog.get("expressions", [])):
                self._set_progress(request_id, "complete", 100, 100)
                return {"resource": resource, "catalog": catalog, "device_id": session.device_id}
            required = len(package.payload) + package.expanded_size_bytes + SPACE_RESERVE_BYTES
            if int(info.get("free_bytes") or 0) < required:
                raise ValueError("存储卡空间不足，请先整理素材")
            protocol.send(f"WORK_BEGIN {package.work_id} {len(package.payload)} {package.sha256} {package.expanded_size_bytes}")
            chunk_size = _negotiate_chunk_size(protocol.receive(10, ("OK",)))
            for sequence, offset in enumerate(range(0, len(package.payload), chunk_size)):
                block = package.payload[offset:offset + chunk_size]
                encoded = base64.b64encode(block).decode("ascii")
                protocol.send(f"DATA {sequence} {zlib.crc32(block):08x} {encoded}")
                ack = protocol.receive(8, ("ACK",))
                if len(ack) < 3 or ack[2] != str(sequence):
                    raise ValueError("设备数据确认不一致，请重新连接")
                self._set_progress(request_id, "uploading", offset + len(block), len(package.payload))
            protocol.send("COMMIT")
            def progress(phase, percent, message):
                self._set_progress(request_id, phase, percent, 100)
            completed = _wait_for_sd_activation(protocol, package.work_id, progress)
            if len(completed) < 3 or completed[2] != package.work_id:
                raise ValueError("导入确认不一致，请重新读取设备")
            # DONE precedes the commit worker releasing its maintenance lock.
            # Retry only that explicit transient response, never a failed write.
            for attempt in range(20):
                try:
                    catalog = _receive_work_document(protocol, "IDLE_RESOURCES", timeout_seconds=60, max_bytes=512 * 1024)
                    break
                except MaintenanceError as exc:
                    if "commit_in_progress" not in str(exc) or attempt == 19:
                        raise
                    time.sleep(0.1)
            if not any(item.get("id") == resource for item in catalog.get("expressions", [])):
                raise ValueError("素材已写入但目录尚不可用，请重新读取设备")
            self._set_progress(request_id, "complete", 100, 100)
            return {"resource": resource, "catalog": catalog, "device_id": session.device_id}
        except Exception:
            self._set_progress(request_id, "failed")
            raise
        finally:
            try:
                if protocol is not None:
                    try:
                        protocol.send("CLOSE")
                    finally:
                        protocol.close()
            finally:
                self._lock.release()

    def read(self, port: str) -> dict[str, Any]:
        if port not in {p["port"] for p in self.ports()}:
            raise ValueError("设备端口已断开，请重新选择")
        if not self._lock.acquire(blocking=False):
            raise ValueError("正在读取设备，请稍候")
        protocol = None
        try:
            protocol = _SerialProtocol(port)
            # Current firmware accepts 115200 only. Do not use the legacy
            # handshake's unconditional 460800 baud transition.
            protocol.send("HELLO")
            try:
                ready = protocol.receive(3, ("READY",))
            except MaintenanceError as exc:
                if '等待设备响应超时' not in str(exc):
                    raise
                raise MaintenanceError(
                    '串口已打开，但设备未响应编辑器协议。请在 Watcher 切换到 AI Chat / JoyInside 模式后重试；'
                    '如果刚切换模式，请等待启动完成。仍失败时，请检查配套固件是否支持编辑器接口。'
                ) from exc
            info = _parse_ready_device_info(ready)
            if info["sd_state"] != "mounted":
                raise ValueError("设备存储卡未就绪")
            if "idle_catalog_v1" in info["capabilities"]:
                catalog = _receive_work_document(protocol, "IDLE_RESOURCES",
                                                 timeout_seconds=60, max_bytes=512 * 1024)
            else:
                catalog = _receive_work_document(protocol, "WORK_RESOURCES")
            result = {"port": port, "device": info, "catalog": catalog,
                      "editable": False, "profile": None,
                      "message": "素材已读取；当前固件尚不支持 JoyInside 行为配置，请完成配套固件升级。"}
            if "joy_idle_v1" in info["capabilities"]:
                result.update(_receive_work_document(protocol, "IDLE_GET"))
                result["editable"] = True
                result["message"] = "已读取这台 Watcher 的待机行为"
            return result
        finally:
            try:
                if protocol is not None:
                    try:
                        protocol.send("CLOSE")
                    finally:
                        protocol.close()
            finally:
                self._lock.release()
