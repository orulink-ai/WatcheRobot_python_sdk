"""Loopback-only service for tuning device-side procedural expressions."""

from __future__ import annotations

import asyncio
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from watcherobot.errors import WatcheRobotError


class ExpressionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str = Field(pattern="^(standby|thinking|speaking)$")
    style: str = Field(
        default="watcher",
        pattern="^(watcher|watcher_compact|watcher_focus|watcher_open|watcher_pulse)$",
    )
    gaze_x: float = Field(default=0.0, ge=-1.0, le=1.0)
    gaze_y: float = Field(default=0.0, ge=-1.0, le=1.0)
    openness: float = Field(default=1.0, ge=0.05, le=1.2)
    spacing: float = Field(default=0.85, ge=0.4, le=1.6)
    scale: float = Field(default=1.0, ge=0.5, le=1.5)
    scale_x: float = Field(default=2.0, ge=0.75, le=2.2)
    scale_y: float = Field(default=2.0, ge=0.75, le=2.2)
    stroke: float = Field(default=1.0, ge=0.5, le=2.0)
    roundness: float = Field(default=1.0, ge=0.0, le=1.0)
    left_openness: float = Field(default=1.0, ge=0.1, le=1.5)
    right_openness: float = Field(default=1.0, ge=0.1, le=1.5)
    tilt_deg: int = Field(default=0, ge=-30, le=30)
    left_tilt_deg: int = Field(default=0, ge=-30, le=30)
    right_tilt_deg: int = Field(default=0, ge=-30, le=30)
    tag: str = Field(default="none", pattern="^(none|thinking|question|love)$")
    accessory: str = Field(
        default="none",
        pattern="^(none|halo|devil_horns|ninja_mask|hero_mask|eyepatch|antenna)$",
    )
    accessory_scale: float = Field(default=1.0, ge=0.25, le=2.0)
    accessory_x: float = Field(default=0.0, ge=-1.0, le=1.0)
    accessory_y: float = Field(default=0.0, ge=-1.0, le=1.0)
    accessory_rotation_deg: int = Field(default=0, ge=-180, le=180)
    auto_blink: bool = True
    blink_interval_ms: int = Field(default=3600, ge=1200, le=10000)
    blink_duration_ms: int = Field(default=200, ge=100, le=800)
    color: str = Field(default="#A1F03C", pattern="^#[0-9A-Fa-f]{6}$")
    sphere_strength: float = Field(default=0.68, ge=0.0, le=1.0)
    transition_ms: int = Field(default=180, ge=0, le=2000)


class ExpressionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str | None = Field(default=None, pattern="^(standby|thinking|speaking)$")
    style: str | None = Field(
        default=None,
        pattern="^(watcher|watcher_compact|watcher_focus|watcher_open|watcher_pulse)$",
    )
    gaze_x: float | None = Field(default=None, ge=-1.0, le=1.0)
    gaze_y: float | None = Field(default=None, ge=-1.0, le=1.0)
    openness: float | None = Field(default=None, ge=0.05, le=1.2)
    spacing: float | None = Field(default=None, ge=0.4, le=1.6)
    scale: float | None = Field(default=None, ge=0.5, le=1.5)
    scale_x: float | None = Field(default=None, ge=0.75, le=2.2)
    scale_y: float | None = Field(default=None, ge=0.75, le=2.2)
    stroke: float | None = Field(default=None, ge=0.5, le=2.0)
    roundness: float | None = Field(default=None, ge=0.0, le=1.0)
    left_openness: float | None = Field(default=None, ge=0.1, le=1.5)
    right_openness: float | None = Field(default=None, ge=0.1, le=1.5)
    tilt_deg: int | None = Field(default=None, ge=-30, le=30)
    left_tilt_deg: int | None = Field(default=None, ge=-30, le=30)
    right_tilt_deg: int | None = Field(default=None, ge=-30, le=30)
    tag: str | None = Field(default=None, pattern="^(none|thinking|question|love)$")
    accessory: str | None = Field(
        default=None,
        pattern="^(none|halo|devil_horns|ninja_mask|hero_mask|eyepatch|antenna)$",
    )
    accessory_scale: float | None = Field(default=None, ge=0.25, le=2.0)
    accessory_x: float | None = Field(default=None, ge=-1.0, le=1.0)
    accessory_y: float | None = Field(default=None, ge=-1.0, le=1.0)
    accessory_rotation_deg: int | None = Field(default=None, ge=-180, le=180)
    auto_blink: bool | None = None
    blink_interval_ms: int | None = Field(default=None, ge=1200, le=10000)
    blink_duration_ms: int | None = Field(default=None, ge=100, le=800)
    color: str | None = Field(default=None, pattern="^#[0-9A-Fa-f]{6}$")
    sphere_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    transition_ms: int | None = Field(default=None, ge=0, le=2000)


class PairWatcherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_code: str = Field(pattern="^[0-9]{6}$")


class ExpressionLabService:
    """Serialize browser edits onto the managed robot Device channel."""

    _PROBE_FAILURE_LIMIT = 3

    def __init__(
        self,
        *,
        robot: Any,
        pair_watcher: Callable[[str], object] | None = None,
    ) -> None:
        self._robot = robot
        self._runtime = robot.expression_runtime
        self._pair_watcher = pair_watcher
        self._lock = threading.RLock()
        self._active = False
        self._parameters: dict[str, object] = {}
        self._probe_failures = 0

    def status(self) -> dict[str, object]:
        with self._lock:
            refresh_device_info = getattr(self._robot, "refresh_device_info", None)
            probe_succeeded = True
            if callable(refresh_device_info):
                try:
                    refresh_device_info(timeout=0.4)
                    self._probe_failures = 0
                except (TimeoutError, WatcheRobotError):
                    self._probe_failures += 1
                    probe_succeeded = self._probe_failures < self._PROBE_FAILURE_LIMIT
            capabilities = tuple(getattr(self._robot, "capabilities", ()))
            device_info = dict(getattr(self._robot, "device_info", {}))
            device_connected = probe_succeeded and bool(capabilities or device_info)
            expression_supported = device_connected and "expression.runtime.v2" in capabilities
            resource_snapshot = dict(getattr(self._robot, "resource_snapshot", {}))
            if not device_connected:
                resource_snapshot = {}
            animation = resource_snapshot.get("animation", {})
            memory = resource_snapshot.get("memory", {})
            psram = memory.get("psram", {}) if isinstance(memory, dict) else {}
            if not isinstance(animation, dict):
                animation = {}
            if not isinstance(psram, dict):
                psram = {}
            if not device_connected:
                self._active = False
            return {
                "active": self._active,
                "device_connected": device_connected,
                "expression_supported": expression_supported,
                "parameters": dict(self._parameters),
                "performance": {
                    "sample_valid": bool(animation.get("sample_valid", False)),
                    "measured_fps": float(animation.get("measured_fps_x100", 0)) / 100.0,
                    "target_fps": float(animation.get("target_fps_x100", 0)) / 100.0,
                    "draw_ms": float(animation.get("draw_ewma_us", 0)) / 1000.0,
                    "frame_buffer_bytes": int(animation.get("frame_buffer_bytes", 0)),
                    "psram_free_bytes": int(psram.get("free_bytes", 0)),
                },
            }

    def start(self, *, preset: str, **parameters: object) -> dict[str, object]:
        with self._lock:
            connection = self.status()
            if not connection["device_connected"]:
                raise RuntimeError("Watcher is not connected")
            if not connection["expression_supported"]:
                raise RuntimeError("connected firmware does not support expression.runtime.v2")
            if self._active:
                self._runtime.stop()
                self._active = False
            try:
                self._runtime.start(preset, **parameters)
            except Exception:
                self._active = False
                try:
                    # A rejected start may already have claimed the device's
                    # animation surface. Stop restores the default expression.
                    self._runtime.stop()
                except Exception:
                    pass
                raise
            self._active = True
            self._parameters = {"preset": preset, **parameters}
            return self.status()

    def pair(self, pairing_code: str) -> dict[str, object]:
        pair_watcher = self._pair_watcher
        if pair_watcher is None:
            raise RuntimeError("Watcher pairing is unavailable in this launch mode")
        pair_watcher(pairing_code)
        return self.status()

    def update(self, **parameters: object) -> dict[str, object]:
        changes = {name: value for name, value in parameters.items() if value is not None}
        if not changes:
            raise ValueError("at least one expression parameter is required")
        with self._lock:
            if not self._active:
                raise RuntimeError("expression runtime is not active")
            self._runtime.update(**changes)
            self._parameters.update(changes)
            return self.status()

    def stop(self) -> dict[str, object]:
        with self._lock:
            if self._active:
                self._runtime.stop()
            self._active = False
            return self.status()


def create_web_app(service: ExpressionLabService, *, web_root: Path) -> FastAPI:
    def web_file(name: str) -> FileResponse:
        return FileResponse(web_root / name, headers={"Cache-Control": "no-store"})

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            service.stop()

    app = FastAPI(title="Watcher Expression Lab", lifespan=lifespan)

    @app.get("/")
    async def index() -> FileResponse:
        return web_file("index.html")

    @app.get("/styles.css")
    async def stylesheet() -> FileResponse:
        return web_file("styles.css")

    @app.get("/app.js")
    async def script() -> FileResponse:
        return web_file("app.js")

    # Retain the first PoC URLs for compatibility with already packaged copies.
    @app.get("/assets/{asset_name}")
    async def asset(asset_name: str) -> FileResponse:
        if asset_name not in {"styles.css", "app.js"}:
            raise HTTPException(status_code=404, detail="asset not found")
        return web_file(asset_name)

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        return service.status()

    @app.post("/api/pair")
    async def pair(request: PairWatcherRequest) -> dict[str, object]:
        try:
            return await asyncio.to_thread(service.pair, request.pairing_code)
        except (WatcheRobotError, RuntimeError, ValueError) as error:
            detail = re.sub(r"(?<![0-9])[0-9]{6}(?![0-9])", "<pairing-code>", str(error))
            raise HTTPException(status_code=409, detail=detail) from error
        except TimeoutError as error:
            raise HTTPException(status_code=504, detail=str(error)) from error

    @app.post("/api/expression/start")
    async def start(request: ExpressionStartRequest) -> dict[str, object]:
        try:
            payload = request.model_dump()
            preset = str(payload.pop("preset"))
            return await asyncio.to_thread(service.start, preset=preset, **payload)
        except (WatcheRobotError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except TimeoutError as error:
            raise HTTPException(status_code=504, detail=str(error)) from error

    @app.post("/api/expression/update")
    async def update(request: ExpressionUpdateRequest) -> dict[str, object]:
        try:
            return await asyncio.to_thread(service.update, **request.model_dump(exclude_none=True))
        except (WatcheRobotError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except TimeoutError as error:
            raise HTTPException(status_code=504, detail=str(error)) from error

    @app.post("/api/expression/stop")
    async def stop() -> dict[str, object]:
        try:
            return await asyncio.to_thread(service.stop)
        except (WatcheRobotError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except TimeoutError as error:
            raise HTTPException(status_code=504, detail=str(error)) from error

    return app
