"""Loopback-only service for tuning device-side procedural expressions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from watcherobot.errors import WatcheRobotError


APPLICATION_ID = "com.orulink.expression_lab"
APPLICATION_VERSION = "0.1.1"
REQUIRED_FIRMWARE_CAPABILITY = "expression.runtime.v3"


@dataclass(frozen=True)
class FirmwareBundle:
    """One immutable, locally bundled firmware package exposed to the browser."""

    path: Path
    filename: str
    size_bytes: int
    sha256: str
    required_capability: str
    source_pull_request: int
    source_commit: str

    @classmethod
    def load(cls, root: Path | None) -> FirmwareBundle | None:
        if root is None:
            return None
        try:
            document = json.loads(
                (root / "firmware-package.json").read_text(encoding="utf-8")
            )
            if document["app_id"] != APPLICATION_ID:
                return None
            if document["app_version"] != APPLICATION_VERSION:
                return None
            filename = str(document["filename"])
            if (
                filename != Path(filename).name
                or not filename.lower().endswith(".zip")
            ):
                return None
            path = root / filename
            payload = path.read_bytes()
            expected_size = int(document["size_bytes"])
            expected_sha256 = str(document["sha256"]).lower()
            if len(payload) != expected_size:
                return None
            if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
                return None
            if hashlib.sha256(payload).hexdigest() != expected_sha256:
                return None
            required_capability = str(document["required_capability"])
            if required_capability != REQUIRED_FIRMWARE_CAPABILITY:
                return None
            source = document["source"]
            source_pull_request = int(source["pull_request"])
            source_commit = str(source["commit"])
            if source_pull_request < 1 or not re.fullmatch(
                r"[0-9a-f]{40}", source_commit
            ):
                return None
            return cls(
                path=path,
                filename=filename,
                size_bytes=expected_size,
                sha256=expected_sha256,
                required_capability=required_capability,
                source_pull_request=source_pull_request,
                source_commit=source_commit,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def status(self, *, required: bool) -> dict[str, object]:
        return {
            "required": required,
            "available": True,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "required_capability": self.required_capability,
            "source_pull_request": self.source_pull_request,
            "source_commit": self.source_commit,
            "download_url": "./api/firmware/download",
        }


def _validate_custom_vector_path(value: str) -> str:
    message = "custom_vector_path must be a valid bounded vector path"
    if (
        not isinstance(value, str)
        or len(value) < 4
        or len(value) > 1588
        or len(value) % 2 != 0
        or re.fullmatch(r"[0-9A-Fa-f]+", value) is None
    ):
        raise ValueError(message)
    encoded = bytes.fromhex(value)
    if encoded[0] != 1 or encoded[1] > 12:
        raise ValueError(message)
    offset = 2
    total_points = 0
    for _ in range(encoded[1]):
        if offset + 2 > len(encoded):
            raise ValueError(message)
        width = encoded[offset]
        point_count = encoded[offset + 1]
        offset += 2
        total_points += point_count
        if (
            width < 1
            or width > 48
            or point_count < 1
            or point_count > 48
            or total_points > 192
            or offset + point_count * 4 > len(encoded)
        ):
            raise ValueError(message)
        for point_offset in range(offset, offset + point_count * 4, 4):
            x = int.from_bytes(encoded[point_offset : point_offset + 2], "big")
            y = int.from_bytes(encoded[point_offset + 2 : point_offset + 4], "big")
            if x >= 412 or y >= 412:
                raise ValueError(message)
        offset += point_count * 4
    if offset != len(encoded):
        raise ValueError(message)
    return value.lower()


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
    left_upper_lid_y: int = Field(default=-80, ge=-80, le=80)
    left_upper_lid_rotation_deg: int = Field(default=0, ge=-45, le=45)
    right_upper_lid_y: int = Field(default=-80, ge=-80, le=80)
    right_upper_lid_rotation_deg: int = Field(default=0, ge=-45, le=45)
    left_lower_lid_y: int = Field(default=80, ge=-80, le=80)
    left_lower_lid_rotation_deg: int = Field(default=0, ge=-45, le=45)
    right_lower_lid_y: int = Field(default=80, ge=-80, le=80)
    right_lower_lid_rotation_deg: int = Field(default=0, ge=-45, le=45)
    tag: str = Field(default="none", pattern="^(none|thinking|question|love)$")
    accessory: str = Field(
        default="none",
        pattern="^(none|halo|devil_horns|ninja_mask|hero_mask|eyepatch|antenna|custom_vector)$",
    )
    accessory_scale: float = Field(default=1.0, ge=0.25, le=2.0)
    accessory_x: float = Field(default=0.0, ge=-1.0, le=1.0)
    accessory_y: float = Field(default=0.0, ge=-1.0, le=1.0)
    accessory_rotation_deg: int = Field(default=0, ge=-180, le=180)
    custom_vector_path: str = "0100"
    custom_accessory_layer: str = Field(default="front", pattern="^(back|front)$")
    auto_blink: bool = True
    blink_interval_ms: int = Field(default=3600, ge=1200, le=10000)
    blink_duration_ms: int = Field(default=200, ge=100, le=800)
    color: str = Field(default="#A1F03C", pattern="^#[0-9A-Fa-f]{6}$")
    sphere_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    transition_ms: int = Field(default=180, ge=0, le=2000)

    @field_validator("custom_vector_path")
    @classmethod
    def validate_custom_vector_path(cls, value: str) -> str:
        return _validate_custom_vector_path(value)


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
    left_upper_lid_y: int | None = Field(default=None, ge=-80, le=80)
    left_upper_lid_rotation_deg: int | None = Field(default=None, ge=-45, le=45)
    right_upper_lid_y: int | None = Field(default=None, ge=-80, le=80)
    right_upper_lid_rotation_deg: int | None = Field(default=None, ge=-45, le=45)
    left_lower_lid_y: int | None = Field(default=None, ge=-80, le=80)
    left_lower_lid_rotation_deg: int | None = Field(default=None, ge=-45, le=45)
    right_lower_lid_y: int | None = Field(default=None, ge=-80, le=80)
    right_lower_lid_rotation_deg: int | None = Field(default=None, ge=-45, le=45)
    tag: str | None = Field(default=None, pattern="^(none|thinking|question|love)$")
    accessory: str | None = Field(
        default=None,
        pattern="^(none|halo|devil_horns|ninja_mask|hero_mask|eyepatch|antenna|custom_vector)$",
    )
    accessory_scale: float | None = Field(default=None, ge=0.25, le=2.0)
    accessory_x: float | None = Field(default=None, ge=-1.0, le=1.0)
    accessory_y: float | None = Field(default=None, ge=-1.0, le=1.0)
    accessory_rotation_deg: int | None = Field(default=None, ge=-180, le=180)
    custom_vector_path: str | None = None
    custom_accessory_layer: str | None = Field(default=None, pattern="^(back|front)$")
    auto_blink: bool | None = None
    blink_interval_ms: int | None = Field(default=None, ge=1200, le=10000)
    blink_duration_ms: int | None = Field(default=None, ge=100, le=800)
    color: str | None = Field(default=None, pattern="^#[0-9A-Fa-f]{6}$")
    sphere_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    transition_ms: int | None = Field(default=None, ge=0, le=2000)

    @field_validator("custom_vector_path")
    @classmethod
    def validate_custom_vector_path(cls, value: str | None) -> str | None:
        return None if value is None else _validate_custom_vector_path(value)


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
        # Connection probes describe what the browser can currently observe.
        # Keep device ownership separately so a transient disconnect cannot
        # make stop() forget to release an expression runtime it started.
        self._runtime_claimed = False
        self._parameters: dict[str, object] = {}
        self._probe_failures = 0

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._status_locked(probe_device=True)

    def _status_locked(self, *, probe_device: bool) -> dict[str, object]:
        """Build a status snapshot while the service lock is held.

        Command ACKs reuse cached device metadata; periodic browser status
        polling remains responsible for the comparatively slow probe.
        """
        refresh_device_info = getattr(self._robot, "refresh_device_info", None)
        probe_succeeded = self._probe_failures < self._PROBE_FAILURE_LIMIT
        if probe_device and callable(refresh_device_info):
            try:
                refresh_device_info(timeout=0.4)
                self._probe_failures = 0
                probe_succeeded = True
            except (TimeoutError, WatcheRobotError):
                self._probe_failures += 1
                probe_succeeded = self._probe_failures < self._PROBE_FAILURE_LIMIT
        capabilities = tuple(getattr(self._robot, "capabilities", ()))
        device_info = dict(getattr(self._robot, "device_info", {}))
        device_connected = probe_succeeded and bool(capabilities or device_info)
        expression_supported = device_connected and "expression.runtime.v3" in capabilities
        vector_accessory_supported = device_connected and "expression.vector_accessory.v1" in capabilities
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
            "vector_accessory_supported": vector_accessory_supported,
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
            connection = self._status_locked(probe_device=True)
            if not connection["device_connected"]:
                raise RuntimeError("Watcher is not connected")
            if not connection["expression_supported"]:
                raise RuntimeError("connected firmware does not support expression.runtime.v3")
            if self._runtime_claimed:
                self._runtime.stop()
                self._active = False
                self._runtime_claimed = False
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
                self._runtime_claimed = False
                raise
            self._active = True
            self._runtime_claimed = True
            self._parameters = {"preset": preset, **parameters}
            return self._status_locked(probe_device=False)

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
            return self._status_locked(probe_device=False)

    def stop(self) -> dict[str, object]:
        with self._lock:
            try:
                if self._runtime_claimed:
                    self._runtime.stop()
            finally:
                self._active = False
                self._runtime_claimed = False
            return self._status_locked(probe_device=False)


def create_web_app(
    service: ExpressionLabService,
    *,
    web_root: Path,
    firmware_root: Path | None = None,
) -> FastAPI:
    firmware_bundle = FirmwareBundle.load(firmware_root)

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

    @app.get("/vector-path.js")
    async def vector_path_script() -> FileResponse:
        return web_file("vector-path.js")

    # Retain the first PoC URLs for compatibility with already packaged copies.
    @app.get("/assets/{asset_name}")
    async def asset(asset_name: str) -> FileResponse:
        if asset_name not in {"styles.css", "vector-path.js", "app.js"}:
            raise HTTPException(status_code=404, detail="asset not found")
        return web_file(asset_name)

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        snapshot = service.status()
        required = bool(
            snapshot["device_connected"] and not snapshot["expression_supported"]
        )
        snapshot["firmware_update"] = (
            firmware_bundle.status(required=required)
            if firmware_bundle is not None
            else {"required": required, "available": False}
        )
        return snapshot

    @app.get("/api/firmware/download")
    async def download_firmware() -> FileResponse:
        if firmware_bundle is None:
            raise HTTPException(status_code=404, detail="firmware package unavailable")
        return FileResponse(
            firmware_bundle.path,
            media_type="application/zip",
            filename=firmware_bundle.filename,
            headers={"Cache-Control": "no-store"},
        )

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
