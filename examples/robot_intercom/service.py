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
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

LOG = logging.getLogger(__name__)
REQUIRED = {"rtc.audio.full_duplex.v1", "expression.audio_follow.v1", "camera.capture"}
MDNS = re.compile(r"(?im)(^(?:a=)?candidate:\S+\s+\d+\s+\S+\s+\d+\s+)(\S+\.local)(\s+\d+\s+typ\s+host\b)")


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
    def __init__(self, *, robot, rtc, device_status, artifacts_dir, clock=time.monotonic):
        self.robot, self.rtc = robot, rtc
        self.device_status = device_status
        self.artifacts_dir = Path(artifacts_dir)
        self.clock = clock
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
            state = dict(request_id=self._owner, state=self._state, error=self._last_error)
        evidence = self.robot.resource_evidence
        received = evidence.get("received_at")
        fresh = (connection.get("online") is True and evidence.get("consistent") is not False
                 and evidence.get("device_id") == self.robot.device_info.get("device_id")
                 and isinstance(received, (float, int)) and 0 <= self.clock() - received < 5)
        return {**state, "connected": connection.get("online") is True,
                "device": self.robot.device_info, "capabilities": sorted(self.robot.capabilities),
                "rtc": self.rtc.snapshot(),
                "resources": evidence.get("snapshot") if fresh else None}

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

    def stop(self, request_id=None):
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
            self._online()
            if self.rtc.snapshot().get("active"):
                self.rtc.stop()
                if self.rtc.snapshot().get("active"):
                    raise IntercomError("通话释放尚未确认，请重试结束通话")
            if self._follow:
                self.robot.expression_runtime.set_audio_follow(False)
                self._follow = False
            with self._state_lock:
                self._owner = self._device_id = self._connection_id = None
                self._state, self._last_error = "idle", None
            return {"matched": True, "state": "idle"}

    def capture(self, request_id):
        # Stop/shutdown drains a photo before releasing the display or transport.
        with self._lifecycle:
            self._before_device_call(request_id)
            image = self.robot.camera.capture(timeout=10.0)
            self._owned(request_id)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            name = f"photo-{uuid.uuid4().hex}.jpg"
            target = self.artifacts_dir / name
            target.write_bytes(bytes(image.data))
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

    def maintain(self):
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
                               else now - self._last_seen > 10)
                    if (self._state == "connecting" and not expired
                            and self.rtc.snapshot().get("state") == "connected"):
                        self._state = "running"
                        self._last_seen = now
                    pending = self._state == "cleanup_required"
                if owner and (expired or pending or not self.device_status().get("online")):
                    try:
                        self.stop(owner)
                    except Exception as error:
                        with self._state_lock:
                            self._last_error = str(error)
            finally:
                self._signal_lock.release()
        finally:
            self._lifecycle.release()

    def close(self):
        self.request_shutdown()
        self.stop()

    def request_shutdown(self):
        self._closing = True


class CallRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=63, pattern=r"^[A-Za-z0-9._:-]+$")


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


def create_web_app(service, *, web_root):
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
            await asyncio.to_thread(service.close)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

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

    @app.exception_handler(Exception)
    async def failure(request, error):
        LOG.exception("Intercom operation failed", exc_info=error)
        return JSONResponse({"error": "机器人操作未完成，请检查连接后重试"}, status_code=502)

    @app.get("/")
    async def index():
        return FileResponse(Path(web_root) / "index.html")

    @app.get("/api/status")
    async def status():
        return await asyncio.to_thread(service.status)

    @app.post("/api/call/start")
    async def start(body: CallRequest):
        return await asyncio.to_thread(service.start, body.request_id)

    @app.post("/api/call/stop")
    async def stop(body: CallRequest):
        return await asyncio.to_thread(service.stop, body.request_id)

    @app.post("/api/photo")
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

    @app.get("/photos/{name}")
    async def artifact(name: str):
        if re.fullmatch(r"photo-[0-9a-f]{32}\.jpg", name) is None:
            return JSONResponse({"error": "照片不存在"}, status_code=404)
        path = service.artifacts_dir / name
        if not path.is_file() or path.is_symlink():
            return JSONResponse({"error": "照片不存在"}, status_code=404)
        return FileResponse(path, media_type="image/jpeg", filename=name,
                            content_disposition_type="inline")

    app.mount("/assets", StaticFiles(directory=web_root), name="assets")
    return app
