"""Service layer for the managed, loopback-only Vision Debug Lab."""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import struct
import threading
import time
import urllib.request
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, TextIO

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel, Field
from watcherobot.errors import CommandError, WatcheRobotError


_LOGGER = logging.getLogger(__name__)
_PACKET_HEADER = struct.Struct("<4sI")
_PACKET_MAGIC = b"VDL1"
_EVENT_LIMIT = 200
_SAMPLE_LIMIT = 512


class VisionLabError(RuntimeError):
    """Base error returned by the local debug surface."""


class VisionLabBusyError(VisionLabError):
    """Raised when a mutually exclusive operation is already active."""


class VisionLabPreflightError(VisionLabError):
    """Raised when device vision state cannot support face preview."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PreviewStartRequest(BaseModel):
    width: int = Field(default=416)
    height: int = Field(default=416)
    frame_stride: int = Field(default=1, ge=1, le=3)
    stop_policy: str = Field(default="hold")


class PreviewStopRequest(BaseModel):
    policy: str = Field(default="hold")


class DaemonDeviceStatusProvider:
    """Read the Runtime-owned device status injected into the Application."""

    def __init__(self, url: str) -> None:
        self._url = url

    def __call__(self) -> dict[str, object]:
        request = urllib.request.Request(self._url, method="GET")
        with urllib.request.urlopen(request, timeout=2.0) as response:
            payload = json.load(response)
        device = payload.get("device", {}) if isinstance(payload, dict) else {}
        return dict(device) if isinstance(device, dict) else {}


def encode_preview_packet(frame: Any) -> bytes:
    """Encode one JPEG and its same-sequence telemetry for the browser."""

    telemetry = frame.telemetry
    metadata = {
        "sequence": int(frame.sequence),
        "timestamp_ms": int(frame.device_timestamp_ms),
        "received_at": float(frame.received_at),
        "application_ingress_ms": round(
            max(0.0, (time.time() - float(frame.received_at)) * 1000),
            3,
        ),
        "width": int(frame.width),
        "height": int(frame.height),
        "jpeg_bytes": len(frame.jpeg),
        "faces": [
            {
                "x": int(face.x),
                "y": int(face.y),
                "width": int(face.width),
                "height": int(face.height),
                "score": int(face.score),
                "target": int(face.target),
            }
            for face in frame.faces
        ],
        "telemetry": {
            "sequence": int(telemetry.sequence),
            "age_ms": int(telemetry.age_ms),
            "target_visible": bool(telemetry.target_visible),
            "error_x_percent": float(telemetry.error_x_percent),
            "error_y_percent": float(telemetry.error_y_percent),
            "pan_velocity_deg_s": float(telemetry.pan_velocity_deg_s),
            "tilt_velocity_deg_s": float(telemetry.tilt_velocity_deg_s),
            "state": int(telemetry.state),
            "command": int(telemetry.command),
            "preprocess_ms": telemetry.preprocess_ms,
            "inference_ms": telemetry.inference_ms,
            "postprocess_ms": telemetry.postprocess_ms,
        },
    }
    encoded = json.dumps(
        metadata,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _PACKET_HEADER.pack(_PACKET_MAGIC, len(encoded)) + encoded + bytes(
        frame.jpeg
    )


def decode_preview_packet(packet: bytes) -> tuple[dict[str, Any], bytes]:
    """Decode the browser packet for tests and offline tooling."""

    if len(packet) < _PACKET_HEADER.size:
        raise ValueError("invalid preview packet")
    magic, metadata_length = _PACKET_HEADER.unpack_from(packet)
    metadata_end = _PACKET_HEADER.size + metadata_length
    if magic != _PACKET_MAGIC or metadata_end >= len(packet):
        raise ValueError("invalid preview packet")
    try:
        metadata = json.loads(
            packet[_PACKET_HEADER.size : metadata_end].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid preview packet metadata") from error
    jpeg = bytes(packet[metadata_end:])
    if not isinstance(metadata, dict) or not (
        jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
    ):
        raise ValueError("invalid preview packet")
    return metadata, jpeg


class VisionDebugLabService:
    """Own preview, recording, diagnostics, and disconnect safety."""

    def __init__(
        self,
        *,
        robot: Any,
        artifacts_dir: Path,
        device_status_provider: Callable[[], Mapping[str, object]],
        disconnect_grace_seconds: float = 2.0,
    ) -> None:
        self._robot = robot
        self._artifacts_dir = Path(artifacts_dir)
        self._device_status_provider = device_status_provider
        self._disconnect_grace_seconds = disconnect_grace_seconds
        self._lock = threading.RLock()
        self._packet_condition = threading.Condition(self._lock)
        self._events: deque[dict[str, object]] = deque(maxlen=_EVENT_LIMIT)
        self._event_sequence = 0
        self._preview: Any | None = None
        self._preview_thread: threading.Thread | None = None
        self._preview_stop = threading.Event()
        self._preview_running = False
        self._stop_requested = False
        self._preview_config: dict[str, object] = {}
        self._last_vision: dict[str, object] | None = None
        self._latest_packet: bytes | None = None
        self._packets: deque[tuple[int, bytes]] = deque(maxlen=8)
        self._packet_generation = 0
        self._started_at_monotonic: float | None = None
        self._elapsed_seconds = 0.0
        self._frames = 0
        self._face_frames = 0
        self._jpeg_bytes = 0
        self._missing_sequences = 0
        self._last_sequence: int | None = None
        self._last_frame_monotonic: float | None = None
        self._gaps_ms: deque[float] = deque(maxlen=_SAMPLE_LIMIT)
        self._ages_ms: deque[float] = deque(maxlen=_SAMPLE_LIMIT)
        self._inference_ms: deque[float] = deque(maxlen=_SAMPLE_LIMIT)
        self._application_ms: deque[float] = deque(maxlen=_SAMPLE_LIMIT)
        self._viewer_count = 0
        self._disconnect_timer: threading.Timer | None = None
        self._recording_file: TextIO | None = None
        self._recording_dir: Path | None = None
        self._recording_relative: Path | None = None
        self._recording_started_at: float | None = None
        self._recording_frames = 0
        self._recording_bytes = 0

    def status(self) -> dict[str, object]:
        connection = self._connection_snapshot()
        with self._lock:
            running = self._preview_running
            cached_vision = dict(self._last_vision or {})
        if running and cached_vision:
            vision = cached_vision
        else:
            vision = self._read_vision_snapshot(connection)
            if vision.get("available") is True:
                with self._lock:
                    self._last_vision = dict(vision)
        session = self._session_snapshot()
        return {
            "connection": connection,
            "vision": vision,
            "session": session,
            "findings": _build_findings(connection, vision, session),
        }

    def events(self, *, after: int = 0) -> list[dict[str, object]]:
        with self._lock:
            return [dict(event) for event in self._events if int(event["id"]) > after]

    def start_preview(
        self,
        *,
        width: int,
        height: int,
        frame_stride: int,
        stop_policy: str,
    ) -> dict[str, object]:
        with self._lock:
            if self._preview_running:
                raise VisionLabBusyError("face preview is already running")
        if (width, height) not in {(240, 240), (416, 416), (640, 480)}:
            raise ValueError("unsupported preview resolution")
        if not 1 <= frame_stride <= 3:
            raise ValueError("frame_stride must be between 1 and 3")
        _validate_policy(stop_policy)
        connection = self._connection_snapshot()
        if connection["online"] is not True:
            raise VisionLabPreflightError("device_offline", "Watcher device is offline")
        vision = self._read_vision_snapshot(connection)
        self._validate_preflight(vision)
        with self._lock:
            if self._preview_running:
                raise VisionLabBusyError("face preview is already running")
        preview = self._robot.face_tracking.open_preview(
            width=width,
            height=height,
            frame_stride=frame_stride,
            stop_policy=stop_policy,
            queue_size=1,
        )
        with self._lock:
            self._preview = preview
            self._preview_running = True
            self._stop_requested = False
            self._preview_stop.clear()
            self._preview_config = {
                "width": width,
                "height": height,
                "frame_stride": frame_stride,
                "stop_policy": stop_policy,
            }
            self._last_vision = dict(vision)
            self._reset_metrics_locked()
            self._started_at_monotonic = time.monotonic()
            thread = threading.Thread(
                target=self._preview_loop,
                args=(preview,),
                name="vision-debug-preview",
                daemon=True,
            )
            self._preview_thread = thread
        self._append_event("preview", "Face preview started", "ok")
        thread.start()
        return self._session_snapshot()

    def stop_preview(self, *, policy: str = "hold") -> dict[str, object]:
        _validate_policy(policy)
        with self._lock:
            running = self._preview_running
            thread = self._preview_thread
            self._stop_requested = True
            self._preview_stop.set()
            self._freeze_elapsed_locked()
            self._preview_running = False
        if running:
            try:
                self._robot.face_tracking.stop(policy=policy)
            except Exception:
                _LOGGER.exception("Vision Debug Lab stop command failed")
                with self._lock:
                    preview = self._preview
                if preview is not None:
                    preview.close()
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=3.0)
            self._append_event(
                "preview",
                f"Face preview stopped with {policy.upper()}",
                "ok",
            )
        if self._recording_file is not None:
            self.stop_recording()
        with self._lock:
            self._preview_running = False
            self._preview = None
            self._preview_thread = None
            self._packet_condition.notify_all()
        return self._session_snapshot()

    def start_recording(self) -> dict[str, object]:
        with self._lock:
            if not self._preview_running:
                raise VisionLabPreflightError(
                    "preview_not_running",
                    "Start face preview before recording",
                )
            if self._recording_file is not None:
                raise VisionLabBusyError("recording is already active")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            suffix = f"{time.time_ns() % 1_000_000:06d}"
            relative = Path("recordings") / f"{stamp}-{suffix}"
            directory = self._artifacts_dir / relative
            directory.joinpath("frames").mkdir(parents=True, exist_ok=False)
            stream = directory.joinpath("frames.jsonl").open(
                "w",
                encoding="utf-8",
                newline="\n",
            )
            self._recording_file = stream
            self._recording_dir = directory
            self._recording_relative = relative
            self._recording_started_at = time.time()
            self._recording_frames = 0
            self._recording_bytes = 0
            result = self._recording_snapshot_locked()
        self._append_event("recording", "Dataset recording started", "record")
        return result

    def stop_recording(self) -> dict[str, object]:
        with self._lock:
            if self._recording_file is None or self._recording_dir is None:
                raise VisionLabPreflightError(
                    "recording_not_running",
                    "No dataset recording is active",
                )
            stream = self._recording_file
            directory = self._recording_dir
            result = self._recording_snapshot_locked()
            stream.flush()
            stream.close()
            manifest = {
                "schema": "watcher.vision-recording.v1",
                **result,
                "ended_at": time.time(),
                "preview": dict(self._preview_config),
                "vision": dict(self._last_vision or {}),
            }
            directory.joinpath("manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._recording_file = None
            self._recording_dir = None
            self._recording_relative = None
            self._recording_started_at = None
        self._append_event(
            "recording",
            f"Dataset recording stopped after {result['frames']} frames",
            "ok",
        )
        return result

    def export_diagnostic_report(self) -> dict[str, object]:
        snapshot = self.status()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        relative = Path("diagnostics") / f"vision-report-{stamp}-{time.time_ns() % 1_000_000:06d}.json"
        path = self._artifacts_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "schema": "watcher.vision-debug-report.v1",
            "generated_at": time.time(),
            **snapshot,
            "events": self.events(),
        }
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._append_event("diagnostics", "Diagnostic report exported", "ok")
        return {
            "relative_path": relative.as_posix(),
            "artifact_url": f"/artifacts/{relative.as_posix()}",
        }

    def artifact_path(self, relative_path: str) -> Path | None:
        root = self._artifacts_dir.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def wait_for_packet(
        self,
        *,
        after_generation: int,
        timeout: float,
        latest: bool = False,
    ) -> tuple[int, bytes]:
        deadline = time.monotonic() + timeout
        with self._packet_condition:
            while self._packet_generation <= after_generation:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("no preview packet before timeout")
                self._packet_condition.wait(timeout=remaining)
            candidates = [
                entry
                for entry in self._packets
                if entry[0] > after_generation
            ]
            packet_entry = candidates[-1] if latest and candidates else (
                candidates[0] if candidates else None
            )
            if packet_entry is None:
                raise TimeoutError("no preview packet before timeout")
            return packet_entry

    def viewer_connected(self) -> None:
        with self._lock:
            self._viewer_count += 1
            timer = self._disconnect_timer
            self._disconnect_timer = None
        if timer is not None:
            timer.cancel()

    def viewer_disconnected(self) -> None:
        with self._lock:
            self._viewer_count = max(0, self._viewer_count - 1)
            if self._viewer_count != 0 or not self._preview_running:
                return
            timer = threading.Timer(
                self._disconnect_grace_seconds,
                self._hold_after_disconnect,
            )
            timer.daemon = True
            self._disconnect_timer = timer
        timer.start()

    def shutdown(self) -> None:
        with self._lock:
            timer = self._disconnect_timer
            self._disconnect_timer = None
        if timer is not None:
            timer.cancel()
        try:
            self.stop_preview(policy="hold")
        except Exception:
            _LOGGER.exception("Vision Debug Lab shutdown failed")

    def _preview_loop(self, preview: Any) -> None:
        unexpected_error: Exception | None = None
        try:
            while not self._preview_stop.is_set():
                try:
                    frame = preview.read(timeout=1.0)
                except TimeoutError:
                    continue
                except Exception as error:
                    if not self._preview_stop.is_set():
                        unexpected_error = error
                    break
                self._handle_frame(frame, preview)
        finally:
            with self._lock:
                stop_requested = self._stop_requested
            if not stop_requested:
                try:
                    preview.close()
                except Exception:
                    _LOGGER.exception("Unable to close face preview")
            with self._lock:
                self._freeze_elapsed_locked()
                self._preview_running = False
                self._preview = None
                self._packet_condition.notify_all()
            if unexpected_error is not None:
                self._append_event(
                    "preview",
                    f"Preview stopped unexpectedly: {unexpected_error}",
                    "error",
                )

    def _handle_frame(self, frame: Any, preview: Any) -> None:
        packet = encode_preview_packet(frame)
        now = time.monotonic()
        with self._packet_condition:
            if self._last_frame_monotonic is not None:
                self._gaps_ms.append((now - self._last_frame_monotonic) * 1000)
            self._last_frame_monotonic = now
            sequence = int(frame.sequence)
            if self._last_sequence is not None:
                stride = max(1, int(self._preview_config.get("frame_stride") or 1))
                delta = max(0, sequence - self._last_sequence)
                self._missing_sequences += max(0, (delta - 1) // stride)
            self._last_sequence = sequence
            self._frames += 1
            self._face_frames += int(bool(frame.faces))
            self._jpeg_bytes += len(frame.jpeg)
            self._ages_ms.append(float(frame.telemetry.age_ms))
            self._application_ms.append(
                max(0.0, (time.time() - float(frame.received_at)) * 1000)
            )
            if frame.telemetry.inference_ms is not None:
                self._inference_ms.append(float(frame.telemetry.inference_ms))
            self._latest_packet = packet
            self._packet_generation += 1
            self._packets.append((self._packet_generation, packet))
            self._freeze_elapsed_locked()
            self._write_recording_frame_locked(frame)
            self._packet_condition.notify_all()
            if hasattr(preview, "dropped_frames"):
                self._preview_config["sdk_dropped_frames"] = int(
                    preview.dropped_frames
                )

    def _write_recording_frame_locked(self, frame: Any) -> None:
        if self._recording_file is None or self._recording_dir is None:
            return
        sequence = int(frame.sequence)
        frame_name = f"{sequence:08d}.jpg"
        self._recording_dir.joinpath("frames", frame_name).write_bytes(
            frame.jpeg
        )
        metadata, _jpeg = decode_preview_packet(encode_preview_packet(frame))
        metadata["jpeg"] = f"frames/{frame_name}"
        self._recording_file.write(
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        self._recording_file.flush()
        self._recording_frames += 1
        self._recording_bytes += len(frame.jpeg)

    def _connection_snapshot(self) -> dict[str, object]:
        try:
            raw = dict(self._device_status_provider())
        except Exception as error:
            raw = {"state": "unavailable", "last_error": str(error)}
        return {
            "online": raw.get("online") is True,
            "state": str(raw.get("state") or "unavailable"),
            "request_id": raw.get("request_id"),
            "last_error": raw.get("last_error"),
        }

    def _read_vision_snapshot(
        self,
        connection: Mapping[str, object],
    ) -> dict[str, object]:
        if connection.get("online") is not True:
            return {
                "available": False,
                "backend": "unavailable",
                "health": "offline",
                "error": connection.get("last_error") or "device_offline",
                "capabilities": {},
                "model": None,
            }
        try:
            status = self._robot.vision.status(timeout=5.0)
        except Exception as error:
            return {
                "available": False,
                "backend": "unavailable",
                "health": "error",
                "error": str(error),
                "capabilities": {},
                "model": None,
            }
        capabilities = status.capabilities
        model = status.model
        return {
            "available": True,
            "backend": str(status.backend),
            "health": str(status.health),
            "status_code": int(status.status_code),
            "initialized": bool(status.initialized),
            "himax_connected": bool(status.connected),
            "streaming": bool(status.streaming),
            "inferencing": bool(status.inferencing),
            "capabilities": {
                "capture": bool(capabilities.capture),
                "preview": bool(capabilities.preview),
                "inference": bool(capabilities.inference),
                "model_info": bool(capabilities.model_info),
                "model_management": bool(capabilities.model_management),
            },
            "model": (
                {
                    "id": int(model.model_id),
                    "name": str(model.name),
                    "task": str(model.task),
                    "contains_face_class": bool(model.contains_face_class),
                }
                if model is not None
                else None
            ),
        }

    def _validate_preflight(self, vision: Mapping[str, object]) -> None:
        if vision.get("available") is not True:
            raise VisionLabPreflightError(
                "vision_status_unavailable",
                str(vision.get("error") or "Vision status is unavailable"),
            )
        capabilities = vision.get("capabilities")
        if not isinstance(capabilities, Mapping) or capabilities.get("inference") is not True:
            raise VisionLabPreflightError(
                "backend_no_inference",
                f"{vision.get('backend')} backend does not expose device inference",
            )
        if capabilities.get("preview") is not True:
            raise VisionLabPreflightError(
                "preview_unavailable",
                "Active firmware does not expose face preview",
            )
        model = vision.get("model")
        if not isinstance(model, Mapping):
            raise VisionLabPreflightError(
                "model_unavailable",
                "Active model metadata is unavailable",
            )
        if model.get("contains_face_class") is not True:
            raise VisionLabPreflightError(
                "model_not_face",
                f"Active model {model.get('name')!r} does not contain a face class",
            )

    def _session_snapshot(self) -> dict[str, object]:
        with self._lock:
            elapsed = self._elapsed_seconds
            if self._preview_running and self._started_at_monotonic is not None:
                elapsed = max(
                    elapsed,
                    time.monotonic() - self._started_at_monotonic,
                )
            fps = self._frames / elapsed if elapsed > 0 else 0.0
            return {
                "running": self._preview_running,
                "configuration": dict(self._preview_config),
                "frames": self._frames,
                "face_frames": self._face_frames,
                "missing_sequences": self._missing_sequences,
                "elapsed_seconds": round(elapsed, 2),
                "fps": round(fps, 2),
                "jpeg_bytes": self._jpeg_bytes,
                "jpeg_avg_bytes": round(self._jpeg_bytes / self._frames)
                if self._frames
                else None,
                "gap_p95_ms": _percentile(self._gaps_ms, 0.95),
                "age_p95_ms": _percentile(self._ages_ms, 0.95),
                "inference_avg_ms": round(statistics.mean(self._inference_ms), 2)
                if self._inference_ms
                else None,
                "inference_p95_ms": _percentile(self._inference_ms, 0.95),
                "application_avg_ms": round(
                    statistics.mean(self._application_ms),
                    3,
                )
                if self._application_ms
                else None,
                "application_p95_ms": _percentile(
                    self._application_ms,
                    0.95,
                ),
                "viewers": self._viewer_count,
                "recording": self._recording_snapshot_locked()
                if self._recording_file is not None
                else {"active": False},
            }

    def _recording_snapshot_locked(self) -> dict[str, object]:
        return {
            "active": self._recording_file is not None,
            "relative_path": self._recording_relative.as_posix()
            if self._recording_relative is not None
            else None,
            "started_at": self._recording_started_at,
            "frames": self._recording_frames,
            "jpeg_bytes": self._recording_bytes,
        }

    def _reset_metrics_locked(self) -> None:
        self._latest_packet = None
        self._packets.clear()
        self._packet_generation = 0
        self._elapsed_seconds = 0.0
        self._frames = 0
        self._face_frames = 0
        self._jpeg_bytes = 0
        self._missing_sequences = 0
        self._last_sequence = None
        self._last_frame_monotonic = None
        self._gaps_ms.clear()
        self._ages_ms.clear()
        self._inference_ms.clear()
        self._application_ms.clear()

    def _freeze_elapsed_locked(self) -> None:
        if self._started_at_monotonic is not None:
            self._elapsed_seconds = max(
                self._elapsed_seconds,
                time.monotonic() - self._started_at_monotonic,
            )

    def _hold_after_disconnect(self) -> None:
        with self._lock:
            should_stop = self._viewer_count == 0 and self._preview_running
            self._disconnect_timer = None
        if should_stop:
            self._append_event(
                "safety",
                "Last viewer disconnected; applying HOLD",
                "warning",
            )
            self.stop_preview(policy="hold")

    def _append_event(self, action: str, message: str, tone: str) -> None:
        with self._lock:
            self._event_sequence += 1
            self._events.append(
                {
                    "id": self._event_sequence,
                    "timestamp": time.time(),
                    "action": action,
                    "message": message,
                    "tone": tone,
                }
            )


def create_web_app(
    service: VisionDebugLabService,
    *,
    web_root: Path,
) -> FastAPI:
    """Create the loopback HTTP/WebSocket surface for the lab."""

    web_root = Path(web_root)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await asyncio.to_thread(service.shutdown)

    app = FastAPI(
        title="WatcheRobot Vision Debug Lab",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def local_security_headers(request: Request, call_next: Any) -> Any:
        if not _is_loopback_host(request.headers.get("host", "")):
            return JSONResponse(
                status_code=421,
                content={"error": "loopback_only"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; script-src 'self'; "
            "style-src 'self'; connect-src 'self' ws://127.0.0.1:* "
            "ws://localhost:*; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(VisionLabBusyError)
    async def busy_handler(
        _request: Request,
        error: VisionLabBusyError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": "busy", "message": str(error)},
        )

    @app.exception_handler(VisionLabPreflightError)
    async def preflight_handler(
        _request: Request,
        error: VisionLabPreflightError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": error.code, "message": str(error)},
        )

    @app.exception_handler(CommandError)
    async def command_error_handler(
        _request: Request,
        error: CommandError,
    ) -> JSONResponse:
        message = f"机器人执行失败：{error.message_type}（{error.reason}）。请查看设备日志后重试。"
        service._append_event("device", message, "error")
        return JSONResponse(
            status_code=502,
            content={
                "error": "device_command_rejected",
                "message": message,
                "command": error.message_type,
                "reason": error.reason,
            },
        )

    @app.exception_handler(WatcheRobotError)
    async def sdk_error_handler(
        _request: Request,
        error: WatcheRobotError,
    ) -> JSONResponse:
        message = str(error)
        service._append_event("sdk", message, "error")
        return JSONResponse(
            status_code=409,
            content={"error": "sdk_state_error", "message": message},
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(
            web_root.joinpath("index.html").read_text(encoding="utf-8")
        )

    @app.get("/assets/app.js")
    async def javascript() -> FileResponse:
        return FileResponse(web_root / "app.js", media_type="text/javascript")

    @app.get("/assets/styles.css")
    async def stylesheet() -> FileResponse:
        return FileResponse(web_root / "styles.css", media_type="text/css")

    @app.get("/assets/preview-packet.mjs")
    async def preview_packet_module() -> FileResponse:
        return FileResponse(
            web_root / "preview-packet.mjs",
            media_type="text/javascript",
        )

    @app.get("/assets/overlay-geometry.mjs")
    async def overlay_geometry_module() -> FileResponse:
        return FileResponse(
            web_root / "overlay-geometry.mjs",
            media_type="text/javascript",
        )

    @app.get("/assets/http-response.mjs")
    async def http_response_module() -> FileResponse:
        return FileResponse(
            web_root / "http-response.mjs",
            media_type="text/javascript",
        )

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        return await asyncio.to_thread(service.status)

    @app.get("/api/events")
    async def events(after: int = 0) -> dict[str, object]:
        return {"events": service.events(after=max(0, after))}

    @app.post("/api/preview/start")
    async def start_preview(request: PreviewStartRequest) -> dict[str, object]:
        return await asyncio.to_thread(
            service.start_preview,
            width=request.width,
            height=request.height,
            frame_stride=request.frame_stride,
            stop_policy=request.stop_policy,
        )

    @app.post("/api/preview/stop")
    async def stop_preview(request: PreviewStopRequest) -> dict[str, object]:
        return await asyncio.to_thread(service.stop_preview, policy=request.policy)

    @app.post("/api/recording/start")
    async def start_recording() -> dict[str, object]:
        return await asyncio.to_thread(service.start_recording)

    @app.post("/api/recording/stop")
    async def stop_recording() -> dict[str, object]:
        return await asyncio.to_thread(service.stop_recording)

    @app.post("/api/diagnostics/export")
    async def export_report() -> dict[str, object]:
        return await asyncio.to_thread(service.export_diagnostic_report)

    @app.get("/artifacts/{relative_path:path}")
    async def artifact(relative_path: str) -> FileResponse:
        path = service.artifact_path(relative_path)
        if path is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(path)

    @app.websocket("/ws/preview")
    async def preview_socket(websocket: WebSocket) -> None:
        host = websocket.headers.get("host", "")
        origin = websocket.headers.get("origin")
        if not _is_loopback_host(host) or origin not in {
            None,
            f"http://{host}",
            f"https://{host}",
        }:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        service.viewer_connected()
        generation = 0
        try:
            while True:
                try:
                    generation, packet = await asyncio.to_thread(
                        service.wait_for_packet,
                        after_generation=generation,
                        timeout=1.0,
                        latest=True,
                    )
                    await websocket.send_bytes(packet)
                    await asyncio.sleep(0.1)
                except TimeoutError:
                    await websocket.send_json({"type": "heartbeat"})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            service.viewer_disconnected()

    return app


def _build_findings(
    connection: Mapping[str, object],
    vision: Mapping[str, object],
    session: Mapping[str, object],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if connection.get("online") is not True:
        findings.append(
            {
                "code": "device_offline",
                "severity": "error",
                "message": "Watcher is not connected to the SDK Runtime.",
            }
        )
    backend = str(vision.get("backend") or "unavailable")
    capabilities = vision.get("capabilities")
    if not isinstance(capabilities, Mapping) or capabilities.get("inference") is not True:
        findings.append(
            {
                "code": "backend_no_inference",
                "severity": "error",
                "message": f"{backend.upper()} does not expose device inference.",
            }
        )
    model = vision.get("model")
    if not isinstance(model, Mapping):
        findings.append(
            {
                "code": "model_unavailable",
                "severity": "warning",
                "message": "The active model descriptor is unavailable.",
            }
        )
    elif model.get("contains_face_class") is not True:
        findings.append(
            {
                "code": "model_not_face",
                "severity": "error",
                "message": f"{model.get('name') or 'Active model'} is not a face model.",
            }
        )
    if vision.get("himax_connected") is False:
        findings.append(
            {
                "code": "himax_disconnected",
                "severity": "error",
                "message": "Himax is not connected to ESP32.",
            }
        )
    if (
        isinstance(capabilities, Mapping)
        and capabilities.get("model_management") is not True
    ):
        findings.append(
            {
                "code": "model_management_read_only",
                "severity": "info",
                "message": "Model metadata is read-only; upload and switching are not exposed.",
            }
        )
    if session.get("running") is True and float(session.get("fps") or 0) < 10:
        findings.append(
            {
                "code": "preview_fps_low",
                "severity": "warning",
                "message": "Preview throughput is below 10 FPS.",
            }
        )
    if (
        vision.get("available") is True
        and not any(
            finding["severity"] in {"error", "warning"}
            for finding in findings
        )
    ):
        findings.append(
            {
                "code": "vision_ready",
                "severity": "ok",
                "message": "Vision backend, Himax, model, and preview contract are ready.",
            }
        )
    return findings


def _validate_policy(policy: str) -> None:
    if policy not in {"hold", "recenter"}:
        raise ValueError("policy must be hold or recenter")


def _is_loopback_host(host: str) -> bool:
    hostname = host.rsplit(":", 1)[0].strip("[]").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def _percentile(values: Any, fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return round(ordered[index], 2)


__all__ = [
    "DaemonDeviceStatusProvider",
    "VisionDebugLabService",
    "VisionLabBusyError",
    "VisionLabPreflightError",
    "create_web_app",
    "decode_preview_packet",
    "encode_preview_packet",
]
