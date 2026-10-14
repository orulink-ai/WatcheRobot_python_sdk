"""LAN intercom lifecycle; all robot controls use the injected SDK channel."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import socket
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from watcherobot.errors import WatcheRobotError

LOG = logging.getLogger(__name__)
REQUIRED = {"rtc.audio.full_duplex.v1", "expression.audio_follow.v1"}
MDNS = re.compile(r"(?im)(^(?:a=)?candidate:\S+\s+\d+\s+\S+\s+\d+\s+)(\S+\.local)(\s+\d+\s+typ\s+host\b)")
PRESENCE_CLOSE_REASONS = frozenset({
    "", "normal closure", "going away", "ping timeout", "keepalive ping timeout",
    "service restart", "server restart", "server shutdown", "shutdown",
    "internal error", "internal server error", "policy violation", "connection closed",
})


def safe_presence_reason(value):
    """Close reasons are untrusted text and may contain URLs or credentials."""
    if value is None:
        return None
    return value if isinstance(value, str) and value in PRESENCE_CLOSE_REASONS else "[redacted]"


class IntercomError(RuntimeError):
    """A user-recoverable connection or lifecycle conflict."""


class DeviceStatus:
    def __init__(self, url):
        self.url = url

    def __call__(self):
        try:
            with urllib.request.urlopen(self.url, timeout=1) as response:
                return json.load(response)["device"]
        except (OSError, ValueError, KeyError):
            return {"online": False, "state": "unavailable"}

    def _manage(self, action, body):
        request = urllib.request.Request(self.url.rstrip("/") + action,
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                result = json.load(response)
            if not isinstance(result, dict) or not isinstance(result.get("device"), dict):
                raise IntercomError("连接响应异常，请重试")
            return result
        except urllib.error.HTTPError:
            # Do not echo Daemon messages or pairing codes into logs/UI.
            raise IntercomError("连接请求未被接受，请检查机器人状态后重试") from None
        except (OSError, ValueError):
            raise IntercomError("无法联系本机连接服务，请确认桌面端正在运行") from None

    def pair(self, code, device_ip=None):
        return self._manage("/pair", {"pairing_code": code, "target_mode": "python_sdk",
                                     **({"device_ip": device_ip} if device_ip else {})})

    def cancel_pairing(self):
        return self._manage("/pair/cancel", {})


def lan_address(connection):
    """Resolve the local interface on the device route, without sending media."""
    host = urllib.parse.urlparse(connection.get("preview_websocket_url") or "").hostname
    if not host:
        return None
    try:
        peer = ipaddress.IPv4Address(host)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect((str(peer), 9))
            local = ipaddress.IPv4Address(route.getsockname()[0])
        return None if local.is_loopback or local.is_unspecified else str(local)
    except (OSError, ValueError):
        return None


class IntercomService:
    def __init__(self, *, robot, rtc, device_status, artifacts_dir, clock=time.monotonic, logger=LOG,
                 device_pairer=None, device_pairing_canceler=None):
        self.robot, self.rtc = robot, rtc
        self.device_status = device_status
        self.device_pairer = device_pairer
        self.device_pairing_canceler = device_pairing_canceler
        self._pairing_request_id = None
        self.artifacts_dir = Path(artifacts_dir)
        self.clock = clock
        self.logger = logger
        self._lifecycle = threading.RLock()
        self._state_lock = threading.RLock()
        self._signal_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._snapshot_connection = None
        self._owner = None
        self._device_id = None
        self._connection_id = None
        self._follow = False
        self._state = "idle"
        self._last_seen = 0.0
        self._connect_due = 0.0
        self._address = None
        self._closing = False
        self._last_error = None
        self._released_requests = {}
        self._presence = None
        self._device_ping_at = 0.0
        self._rtc_cursor = 0
        self._device_end_reason = None
        self._end_reason = None
        self._history = deque(maxlen=128)

    def _record(self, kind, **data):
        with self._state_lock:
            event = {"at_ms": round(self.clock() * 1000), "request_id": self._owner,
                     "kind": kind, **data}
            self._history.append(event)
        self.logger.info("INTERCOM %s", json.dumps(event, ensure_ascii=False))

    def diagnostic(self, request_id, data):
        with self._signal_lock:
            self._owned(request_id)
            allowed = {"connection_state", "ice_state", "visibility", "sent", "received", "lost",
                       "close_code", "close_reason", "was_clean", "error_seen",
                       "rtt_ms", "receive_buffer_ms", "receive_target_ms", "receive_minimum_ms",
                       "receiver_hint_supported",
                       "capture_ms", "context_ms", "playback_mode", "voice_mode"}
            evidence = {key: value for key, value in data.items() if key in allowed}
            if "close_reason" in evidence:
                evidence["close_reason"] = safe_presence_reason(evidence["close_reason"])
            self._record(data["kind"], **evidence)
        return {"ok": True}

    def attach_presence(self, request_id):
        with self._signal_lock:
            self._owned(request_id)
            self._online()
            if self._presence is not None:
                raise IntercomError("当前页面已建立通话连接")
            self._presence = uuid.uuid4().hex
            self._record("presence_open")
            return self._presence

    def detach_presence(self, request_id, token, *, close_code=None, close_reason=None,
                        close_kind="disconnect"):
        with self._signal_lock:
            if request_id != self._owner or token != self._presence:
                return
            self._presence = None
            with self._state_lock:
                self._state = "cleanup_required"
                # Maintenance may acquire the lifecycle lock before stop().
                # Preserve the observed loss before that cleanup can retry.
                if self._end_reason is None:
                    self._end_reason = "presence_lost"
            self._record("presence_closed", close_code=close_code,
                         close_reason=safe_presence_reason(close_reason), close_kind=close_kind,
                         shutdown=self._closing,
                         heartbeat_age_ms=round((self.clock() - self._last_seen) * 1000))
        try:
            self.stop(request_id, reason="presence_lost")
        except Exception as error:
            with self._state_lock:
                self._last_error = str(error)

    def status(self):
        connection = self.device_status()
        connection_key = (connection.get("request_id"), connection.get("connection_id"))
        if connection.get("online") is True and (not self.robot.capabilities or connection_key != self._snapshot_connection):
            if self._refresh_lock.acquire(blocking=False):
                try:
                    self.robot.refresh_device_info(timeout=2.0)
                    self._snapshot_connection = connection_key
                except Exception:
                    LOG.warning("Device information is not ready yet", exc_info=True)
                finally:
                    self._refresh_lock.release()
        elif connection.get("online") is not True:
            self._snapshot_connection = None
        with self._state_lock:
            state = dict(request_id=self._owner, state=self._state, error=self._last_error,
                         diagnostics={"end_reason": self._end_reason,
                                      "device_end_reason": self._device_end_reason,
                                      "heartbeat_age_ms": round((self.clock() - self._last_seen) * 1000),
                                      "presence_connected": self._presence is not None,
                                      "history": list(self._history)})
        evidence = self.robot.resource_evidence
        received = evidence.get("received_at")
        fresh = (connection.get("online") is True and evidence.get("consistent") is not False
                 and evidence.get("device_id") == self.robot.device_info.get("device_id")
                 and isinstance(received, (float, int)) and 0 <= self.clock() - received < 5)
        # Only expose connection state/identity, never transport credentials.
        safe_connection = {key: connection.get(key) for key in
                           ("state", "online", "request_id", "device_id", "last_error", "reuniting")}
        return {**state, "connected": connection.get("online") is True, "connection": safe_connection,
                "device": self.robot.device_info, "capabilities": sorted(self.robot.capabilities),
                "rtc": self.rtc.snapshot(),
                "resources": evidence.get("snapshot") if fresh else None}

    def pair_device(self, code, device_ip=None):
        with self._lifecycle, self._signal_lock:
            if self._closing or self._owner is not None:
                raise IntercomError("请先完成当前通话的释放，再连接机器人")
            if self.device_status().get("online"):
                raise IntercomError("机器人已经连接，可以直接开始通话")
            if self.device_pairer is None:
                raise IntercomError("当前运行环境不支持页面配对，请在桌面端连接")
            result = self.device_pairer(code, device_ip)
            connection = result.get("device") if isinstance(result, dict) else None
            if not isinstance(connection, dict):
                raise IntercomError("连接响应异常，请重试")
            self._pairing_request_id = connection.get("request_id")
            return {"accepted": True, "connection": {key: connection.get(key) for key in
                    ("state", "online", "request_id", "last_error")}}

    def cancel_pairing(self):
        with self._lifecycle, self._signal_lock:
            if self._owner is not None or self._closing:
                raise IntercomError("当前通话尚未释放，无法更改连接")
            connection = self.device_status()
            if connection.get("online"):
                return {"connected": True}
            if not self._pairing_request_id or connection.get("request_id") != self._pairing_request_id:
                raise IntercomError("连接请求已变化，请刷新后查看当前机器人状态")
            if self.device_pairing_canceler is None:
                raise IntercomError("当前运行环境不支持取消配对")
            self.device_pairing_canceler()
            self._pairing_request_id = None
            return {"canceled": True}

    def _online(self):
        connection = self.device_status()
        if connection.get("online") is not True:
            raise IntercomError("机器人未连接，请先在桌面连接机器人")
        if self._device_id is not None:
            current = self.robot.device_info.get("device_id")
            daemon_id = connection.get("device_id", current)
            if current != self._device_id or daemon_id != self._device_id:
                raise IntercomError("请重新连接原机器人以完成通话释放")
            if (connection.get("request_id") or connection.get("connection_id")) != self._connection_id:
                raise IntercomError("设备配对已变化，无法释放原通话；请确认原机器人已结束通话后重启应用")
        return connection

    def _owned(self, request_id):
        with self._state_lock:
            if request_id != self._owner or self._owner is None:
                raise IntercomError("该页面不拥有当前通话，请刷新页面")
            if self._state in {"starting", "connecting"} and self.clock() >= self._connect_due:
                self._state = "cleanup_required"
                self._last_error = "通话连接超时，请重新开始"
                raise IntercomError(self._last_error)
            if self._state not in {"starting", "connecting", "running"}:
                raise IntercomError("通话正在释放，请稍候")

    def _before_device_call(self, request_id):
        self._owned(request_id)
        self._online()
        # Status queries may themselves cross the deadline. Check again next
        # to the device side effect, after all potentially slow preparation.
        self._owned(request_id)

    def start(self, request_id):
        with self._lifecycle:
            if self._closing:
                raise IntercomError("应用正在退出")
            if request_id in self._released_requests:
                raise IntercomError("该通话请求已结束，请重新开始通话")
            if self._owner is not None:
                if self._owner == request_id and self._state in {"connecting", "running"}:
                    self._before_device_call(request_id)
                    return self.status()
                raise IntercomError("已有通话或待释放资源，请先结束通话")
            connection = self._online()
            if not (connection.get("request_id") or connection.get("connection_id")):
                raise IntercomError("设备配对身份尚未确认，请重新连接")
            self.robot.refresh_device_info(timeout=2.0)
            missing = REQUIRED.difference(self.robot.capabilities)
            if missing:
                raise IntercomError("请升级机器人固件，缺少：" + ", ".join(sorted(missing)))
            device_id = self.robot.device_info.get("device_id")
            if not device_id or connection.get("device_id", device_id) != device_id:
                raise IntercomError("机器人身份尚未确认，请重新连接")
            with self._state_lock:
                self._owner, self._device_id = request_id, device_id
                self._connection_id = connection.get("request_id") or connection.get("connection_id")
                self._state, self._last_error = "starting", None
                self._last_seen = self.clock()
                self._connect_due = self._last_seen + 30
                self._address = lan_address(connection)
                self._follow = True  # Enable may be applied even when its ACK is lost.
                self._presence = None
                self._device_ping_at = self.clock()
                self._rtc_cursor = 0
                self._device_end_reason = self._end_reason = None
                self._history.clear()
            self._record("call_start")
            try:
                self._before_device_call(request_id)
                self.robot.expression_runtime.set_audio_follow(True)
                self._before_device_call(request_id)
                self.rtc.start(mode="audio")
                with self._state_lock:
                    if self.clock() >= self._connect_due:
                        raise IntercomError("通话连接超时，请重新开始")
                    self._state = "running" if self.rtc.snapshot().get("state") == "connected" else "connecting"
                    self._last_seen = self.clock()
                return self.status()
            except Exception:
                with self._state_lock:
                    self._state = "cleanup_required"
                try:
                    self.stop(request_id)
                except Exception:
                    LOG.exception("Intercom startup cleanup remains pending")
                raise

    def stop(self, request_id=None, *, reason="user"):
        with self._lifecycle, self._signal_lock:
            # Remember scoped cancellations even if their start request has
            # not reached the owner yet. Bounded replay history prevents a
            # delayed allocation from undoing the browser's confirmed release.
            retired = request_id or self._owner
            if retired is not None:
                self._released_requests[retired] = True
                if len(self._released_requests) > 256:
                    self._released_requests.pop(next(iter(self._released_requests)))
            if self._owner is None:
                return {"matched": True, "state": "idle"}
            if request_id is not None and request_id != self._owner:
                return {"matched": False, "state": self._state}
            with self._state_lock:
                self._state = "cleanup_required"
                if self._end_reason is None:
                    self._end_reason = reason
            self._record("stop_requested", reason=reason, shutdown=self._closing,
                         heartbeat_age_ms=round((self.clock() - self._last_seen) * 1000))
            self._online()
            if self.rtc.snapshot().get("active"):
                self.rtc.stop()
                if self.rtc.snapshot().get("active"):
                    raise IntercomError("通话释放尚未确认，请重试结束通话")
            if self._follow:
                self.robot.expression_runtime.set_audio_follow(False)
                self._follow = False
            self._record("released", reason=reason)
            with self._state_lock:
                self._owner = self._device_id = self._connection_id = None
                self._presence = None
                self._state, self._last_error = "idle", None
            return {"matched": True, "state": "idle"}

    def capture(self, request_id):
        # Stop/shutdown drains a photo before releasing the display or transport.
        with self._lifecycle:
            self._before_device_call(request_id)
            self._record("photo_requested")
            try:
                image = self.robot.camera.capture(timeout=10.0)
            except WatcheRobotError as error:
                reason = getattr(error, "reason", "camera_failed")
                self._record("photo_failed", reason=reason, session_id=getattr(error, "session_id", None))
                message = ("照片回传失败，请重试。通话不受影响。" if reason == "image_upload_failed"
                           else "机器人相机未能完成拍照，请重试。通话不受影响。")
                raise IntercomError(message) from error
            except TimeoutError as error:
                self._record("photo_failed", reason="timeout")
                raise IntercomError("照片获取超时，请稍后重试。通话不受影响。") from error
            self._owned(request_id)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            name = f"photo-{uuid.uuid4().hex}.jpg"
            target = self.artifacts_dir / name
            target.write_bytes(bytes(image.data))
            self._record("photo_completed", bytes=target.stat().st_size)
            return {"artifact": name, "url": f"/photos/{name}", "bytes": target.stat().st_size}

    def signal(self, request_id, data):
        with self._signal_lock:
            self._owned(request_id)
            value = data.get("sdp") if data.get("kind") == "offer" else data.get("candidate")
            if not isinstance(value, str) or not value:
                raise ValueError("缺少 RTC 信令内容")
            if MDNS.search(value):
                if self._address is None:
                    raise IntercomError("无法确定电脑局域网地址")
                value = MDNS.sub(lambda match: match[1] + self._address + match[3], value)
            self._before_device_call(request_id)
            if data["kind"] == "offer":
                self.rtc.send_offer(value)
            else:
                self.rtc.send_candidate(value, sdp_mid=data.get("sdp_mid", "0"),
                                        sdp_mline_index=data.get("sdp_mline_index", 0))
            self._owned(request_id)
            return {"ok": True}

    def heartbeat(self, request_id, browser_send_us):
        with self._signal_lock:
            self._before_device_call(request_id)
            self.rtc.clock_ping(browser_send_us)
            self._owned(request_id)
            with self._state_lock:
                self._last_seen = self.clock()
            return {"ok": True}

    def events(self, request_id, after):
        with self._signal_lock:
            self._owned(request_id)
            return {"events": self.rtc.events(after=after)}

    def feedback(self, request_id, metrics):
        with self._signal_lock:
            self._before_device_call(request_id)
            self.rtc.feedback(**metrics)
            self._owned(request_id)
            return {"ok": True}

    def _maintain_signal(self):
        # Independent of the photo/lifecycle lock and of browser JS timers.
        if not self._signal_lock.acquire(blocking=False):
            return
        try:
            with self._state_lock:
                owner, state = self._owner, self._state
                live = self._presence is not None or self.clock() - self._last_seen <= 10
            if not owner:
                return
            for event in self.rtc.events(after=self._rtc_cursor):
                self._rtc_cursor = max(self._rtc_cursor, event["id"])
                message = event.get("message", {})
                if message.get("type") == "evt.rtc.state":
                    data = message.get("data", {})
                    self._record("device_state", state=data.get("state"), reason=data.get("reason"))
                    if (data.get("state") in {"failed", "stopping", "stopped"}
                            and state in {"connecting", "running"}):
                        self._device_end_reason = data.get("reason") or data["state"]
            if (state in {"connecting", "running"} and live and self._device_end_reason is None
                    and (state == "running" or self.clock() < self._connect_due)
                    and self.clock() - self._device_ping_at >= 1):
                self._before_device_call(owner)
                self.rtc.clock_ping(time.time_ns() // 1000)
                self._owned(owner)
                self._device_ping_at = self.clock()
        except Exception as error:
            with self._state_lock:
                changed = self._last_error != str(error)
                self._last_error = str(error)
            if changed:
                self._record("keepalive_error", error=type(error).__name__)
        finally:
            self._signal_lock.release()

    def maintain(self):
        self._maintain_signal()
        if not self._lifecycle.acquire(blocking=False):
            return
        try:
            # Heartbeats and signals may be awaiting bounded SDK sends. Check
            # expiry only after their state update, without blocking maintenance.
            if not self._signal_lock.acquire(blocking=False):
                return
            try:
                with self._state_lock:
                    owner = self._owner
                    now = self.clock()
                    expired = (now >= self._connect_due if self._state == "connecting"
                               else self._presence is None and now - self._last_seen > 10)
                    if (self._state == "connecting" and not expired
                            and self.rtc.snapshot().get("state") == "connected"):
                        self._state = "running"
                        self._last_seen = now
                    pending = self._state == "cleanup_required"
                    terminal = self._device_end_reason is not None
                offline = owner and not self.device_status().get("online")
                if owner and (expired or pending or terminal or offline):
                    try:
                        reason = "device_terminal" if terminal else "page_expired" if expired else "device_offline" if offline else "cleanup_retry"
                        self.stop(owner, reason=reason)
                    except Exception as error:
                        with self._state_lock:
                            self._last_error = str(error)
            finally:
                self._signal_lock.release()
        finally:
            self._lifecycle.release()

    def close(self):
        self.request_shutdown()
        self.stop(reason="app_shutdown")

    def request_shutdown(self):
        self._closing = True


class CallRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=63, pattern=r"^[A-Za-z0-9._:-]+$")


class DevicePairRequest(BaseModel):
    pairing_code: str = Field(pattern=r"^[0-9]{6}$")
    device_ip: str | None = Field(default=None, max_length=15)

    @field_validator("device_ip")
    @classmethod
    def valid_ip(cls, value):
        return str(ipaddress.IPv4Address(value)) if value is not None else None


class StopRequest(CallRequest):
    reason: str = Field(default="user", max_length=40, pattern=r"^[a-z_]+$")


class DiagnosticRequest(CallRequest):
    receiver_hint_supported: bool | None = Field(default=None, strict=True)
    rtt_ms: int | None = Field(default=None, ge=0, le=60000, strict=True)
    receive_buffer_ms: int | None = Field(default=None, ge=0, le=60000, strict=True)
    receive_target_ms: int | None = Field(default=None, ge=0, le=60000, strict=True)
    receive_minimum_ms: int | None = Field(default=None, ge=0, le=60000, strict=True)
    capture_ms: int | None = Field(default=None, ge=0, le=60000, strict=True)
    context_ms: int | None = Field(default=None, ge=0, le=60000, strict=True)
    playback_mode: str | None = Field(default=None, pattern=r"^(raw|denoised)$")
    voice_mode: str | None = Field(default=None, pattern=r"^(original|deep|bright|robot)$")
    kind: str = Field(pattern=r"^(peer_state|visibility|media_sample|browser_failure|presence_lost)$")
    connection_state: str | None = Field(default=None, max_length=24, pattern=r"^[a-z]+$")
    ice_state: str | None = Field(default=None, max_length=24, pattern=r"^[a-z]+$")
    visibility: str | None = Field(default=None, pattern=r"^(visible|hidden)$")
    sent: int | None = Field(default=None, ge=0, strict=True)
    received: int | None = Field(default=None, ge=0, strict=True)
    lost: int | None = Field(default=None, ge=0, strict=True)
    close_code: int | None = Field(default=None, ge=0, le=65535, strict=True)
    close_reason: str | None = Field(default=None, max_length=123)
    was_clean: bool | None = Field(default=None, strict=True)
    error_seen: bool | None = Field(default=None, strict=True)

    @field_validator("close_reason")
    @classmethod
    def redact_close_reason(cls, value):
        return safe_presence_reason(value)


class SignalRequest(CallRequest):
    kind: str = Field(pattern=r"^(offer|candidate)$")
    sdp: str | None = Field(default=None, max_length=16384)
    candidate: str | None = Field(default=None, max_length=2048)
    sdp_mid: str = Field(default="0", max_length=64)
    sdp_mline_index: int = Field(default=0, ge=0, le=65535)


class HeartbeatRequest(CallRequest):
    browser_send_us: int = Field(gt=0, le=9_007_199_254_740_991, strict=True)


class FeedbackRequest(CallRequest):
    display_fps_x100: int = Field(default=0, ge=0, strict=True)
    frame_age_p95_us: int = Field(default=0, ge=0, strict=True)
    rtt_us: int = Field(default=0, ge=0, strict=True)
    audio_queue_ms: int = Field(default=0, ge=0, strict=True)
    audio_packet_loss_x100: int = Field(default=0, ge=0, strict=True)
    audio_jitter_us: int = Field(default=0, ge=0, strict=True)
    audio_concealed_frames: int = Field(default=0, ge=0, strict=True)
    congestion_level: int = Field(default=0, ge=0, le=3, strict=True)


def create_web_app(service, *, web_root, enable_photo=False):
    presence_cleanup_tasks = set()

    async def cleanup_presence(registration, request_id, close_details):
        try:
            token = await registration
        except IntercomError:
            return
        await asyncio.to_thread(service.detach_presence, request_id, token, **close_details)

    def cleanup_done(task):
        presence_cleanup_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            LOG.error("Presence cleanup failed", exc_info=(type(error), error, error.__traceback__))

    @asynccontextmanager
    async def lifespan(app):
        done = asyncio.Event()
        async def maintenance():
            while not done.is_set():
                await asyncio.to_thread(service.maintain)
                try:
                    await asyncio.wait_for(done.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
        worker = asyncio.create_task(maintenance())
        try:
            yield
        finally:
            done.set()
            await worker
            if presence_cleanup_tasks:
                await asyncio.gather(*presence_cleanup_tasks, return_exceptions=True)
            await asyncio.to_thread(service.close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.presence_cleanup_tasks = presence_cleanup_tasks

    @app.middleware("http")
    async def same_origin(request, call_next):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "不允许外部网页控制机器人"}, status_code=403)
        if request.url.hostname not in {"127.0.0.1", "localhost", "testserver"}:
            return JSONResponse({"error": "仅允许本机访问"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(IntercomError)
    async def conflict(request, error):
        return JSONResponse({"error": str(error)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        message = ("请检查输入：连接码需为六位数字，IP 地址需为有效 IPv4 地址"
                   if request.url.path == "/api/device/pair" else "请求格式无效，请检查输入")
        return JSONResponse({"error": message}, status_code=422)

    @app.exception_handler(Exception)
    async def failure(request, error):
        LOG.exception("Intercom operation failed", exc_info=error)
        return JSONResponse({"error": "机器人操作未完成，请检查连接后重试"}, status_code=502)

    @app.get("/")
    async def index():
        return FileResponse(Path(web_root) / "index.html")

    @app.get("/api/status")
    async def status():
        result = await asyncio.to_thread(service.status)
        return {**result, "features": {"photo": enable_photo}}

    @app.post("/api/call/start")
    async def start(body: CallRequest):
        return await asyncio.to_thread(service.start, body.request_id)

    @app.post("/api/device/pair", status_code=202)
    async def pair_device(body: DevicePairRequest):
        return await asyncio.to_thread(service.pair_device, body.pairing_code,
                                       str(body.device_ip) if body.device_ip is not None else None)

    @app.post("/api/device/pair/cancel")
    async def cancel_pairing():
        return await asyncio.to_thread(service.cancel_pairing)

    @app.post("/api/call/stop")
    async def stop(body: StopRequest):
        return await asyncio.to_thread(service.stop, body.request_id, reason=body.reason)

    @app.post("/api/call/diagnostic")
    async def diagnostic(body: DiagnosticRequest):
        return await asyncio.to_thread(service.diagnostic, body.request_id, body.model_dump(exclude_none=True))

    @app.websocket("/api/call/presence")
    async def presence(socket: WebSocket):
        origin = socket.headers.get("origin")
        expected = str(socket.url.replace(scheme="http", path="", query="")).rstrip("/")
        request_id = socket.query_params.get("request_id", "")
        if (origin != expected or socket.url.hostname not in {"127.0.0.1", "localhost", "testserver"}
                or not re.fullmatch(r"[A-Za-z0-9._:-]{8,63}", request_id)):
            await socket.close(code=1008)
            return
        # to_thread continues after its waiter is cancelled. Retain registration
        # and its eventual token so cancellation cannot leave a phantom page.
        registration = asyncio.create_task(asyncio.to_thread(service.attach_presence, request_id))
        close_details = {"close_kind": "handler_exit"}
        try:
            await asyncio.shield(registration)
            await socket.accept()
            await socket.send_json({"ready": True})
            while True:
                await socket.receive_text()
        except WebSocketDisconnect as error:
            close_details = {"close_kind": "disconnect", "close_code": error.code,
                             "close_reason": error.reason}
        except asyncio.CancelledError:
            close_details = {"close_kind": "cancelled"}
            raise
        except IntercomError:
            await socket.close(code=1008)
        finally:
            cleanup = asyncio.create_task(cleanup_presence(registration, request_id, close_details))
            presence_cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(cleanup_done)
            await asyncio.shield(cleanup)

    async def photo(body: CallRequest):
        return await asyncio.to_thread(service.capture, body.request_id)

    @app.post("/api/call/signal")
    async def signal(body: SignalRequest):
        return await asyncio.to_thread(service.signal, body.request_id, body.model_dump())

    @app.post("/api/call/heartbeat")
    async def heartbeat(body: HeartbeatRequest):
        return await asyncio.to_thread(service.heartbeat, body.request_id, body.browser_send_us)

    @app.post("/api/call/feedback")
    async def feedback(body: FeedbackRequest):
        return await asyncio.to_thread(service.feedback, body.request_id, body.model_dump(exclude={"request_id"}))

    @app.get("/api/call/events")
    async def events(request_id: str, after: int = 0):
        return await asyncio.to_thread(service.events, request_id, max(0, after))

    async def artifact(name: str):
        if re.fullmatch(r"photo-[0-9a-f]{32}\.jpg", name) is None:
            return JSONResponse({"error": "照片不存在"}, status_code=404)
        path = service.artifacts_dir / name
        if not path.is_file() or path.is_symlink():
            return JSONResponse({"error": "照片不存在"}, status_code=404)
        return FileResponse(path, media_type="image/jpeg", filename=name,
                            content_disposition_type="inline")

    if enable_photo:
        app.add_api_route("/api/photo", photo, methods=["POST"])
        app.add_api_route("/photos/{name}", artifact, methods=["GET"])

    app.mount("/assets", StaticFiles(directory=web_root), name="assets")
    return app
