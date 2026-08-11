"""Local-only HTTP service for the standalone SDK Media Lab Application."""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
import urllib.request
import wave
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


class MediaLabBusyError(RuntimeError):
    """Raised when a second hardware action overlaps the active action."""


class MediaLabDeviceOfflineError(RuntimeError):
    """Raised when a hardware action is attempted without an online Watcher."""


class DaemonDeviceStatusProvider:
    """Read the Daemon's loopback-only device status endpoint and fail closed."""

    def __init__(self, url: str, *, timeout: float = 0.5) -> None:
        self._url = url
        self._timeout = timeout

    def __call__(self) -> Mapping[str, object]:
        try:
            request = urllib.request.Request(self._url, method="GET")
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            device = payload.get("device") if isinstance(payload, dict) else None
            if not isinstance(device, dict):
                raise ValueError("Daemon device status response is malformed")
            return device
        except Exception:
            return {
                "online": False,
                "state": "unavailable",
                "last_error": "status_unavailable",
            }


class RecordMicrophoneRequest(BaseModel):
    duration: float = Field(default=5.0, gt=0.0, le=30.0, allow_inf_nan=False)


class MediaLabService:
    """Serialize media actions and expose stable, JSON-ready diagnostics."""

    _ARTIFACT_TYPES = {
        "camera.jpg": "image/jpeg",
        "microphone.wav": "audio/wav",
    }

    def __init__(
        self,
        *,
        robot: Any,
        artifacts_dir: Path,
        sample_audio: Path,
        device_status_provider: Callable[[], Mapping[str, object]],
    ) -> None:
        self._robot = robot
        self._artifacts_dir = Path(artifacts_dir)
        self._sample_audio = Path(sample_audio)
        self._device_status_provider = device_status_provider
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_action: str | None = None
        self._event_sequence = 0
        self._events: deque[dict[str, object]] = deque(maxlen=160)
        self._device_refresh_lock = threading.Lock()
        self._refreshed_connection_token: str | None = None
        self._append_event("system", "Media Lab ready", "ok")

    def status(self) -> dict[str, object]:
        with self._state_lock:
            active_action = self._active_action
        artifacts: dict[str, dict[str, object]] = {}
        for filename, content_type in self._ARTIFACT_TYPES.items():
            path = self._artifacts_dir / filename
            if path.is_file():
                artifacts[filename] = {
                    "bytes": path.stat().st_size,
                    "content_type": content_type,
                    "updated_at": path.stat().st_mtime,
                    "url": f"/artifacts/{filename}?v={path.stat().st_mtime_ns}",
                }
        connection = self._device_status()
        self._refresh_device_snapshot(connection)
        return {
            "connected": connection.get("online") is True,
            "connection": connection,
            "busy": active_action is not None,
            "active_action": active_action,
            "capabilities": list(self._robot.capabilities),
            "device": dict(self._robot.device_info),
            "artifacts": artifacts,
            "events": self.events(),
        }

    def events(self, *, after: int = 0) -> list[dict[str, object]]:
        with self._state_lock:
            return [
                dict(event)
                for event in self._events
                if isinstance((event_id := event.get("id")), int)
                and event_id > after
            ]

    def play_audio(self) -> dict[str, object]:
        with self._operation("play_audio"):
            if not self._sample_audio.is_file():
                raise FileNotFoundError(f"sample audio is missing: {self._sample_audio}")
            playback = self._robot.audio.play_file(self._sample_audio)
            playback.wait(30.0)
            return {
                "source": self._sample_audio.name,
                "bytes": self._sample_audio.stat().st_size,
            }

    def stop_audio(self) -> dict[str, object]:
        self._ensure_device_online()
        self._robot.audio.stop()
        self._append_event("stop_audio", "Audio stop requested", "ok")
        return {"stopped": True}

    def capture_photo(self) -> dict[str, object]:
        with self._operation("capture_photo"):
            image = self._robot.camera.capture(
                width=0,
                height=0,
                quality=0,
                timeout=10.0,
            )
            output = self._artifact_output("camera.jpg")
            output.write_bytes(bytes(image.data))
            return {
                "artifact": output.name,
                "bytes": output.stat().st_size,
                "content_type": "image/jpeg",
            }

    def record_microphone(self, *, duration: float) -> dict[str, object]:
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration))
            or not 0.0 < float(duration) <= 30.0
        ):
            raise ValueError("duration must be a finite number between 0 and 30 seconds")
        duration = float(duration)
        with self._operation("record_microphone"):
            recording = self._robot.microphone.record_pcm(
                duration=duration,
                timeout=duration + 2.0,
                queue_size=32,
            )
            output = self._artifact_output("microphone.wav")
            with wave.open(str(output), "wb") as wav_file:
                wav_file.setnchannels(recording.format.channels)
                wav_file.setsampwidth(recording.format.sample_width_bytes)
                wav_file.setframerate(recording.format.sample_rate_hz)
                wav_file.writeframes(recording.data)
            return {
                "artifact": output.name,
                "bytes": output.stat().st_size,
                "content_type": "audio/wav",
                "duration_seconds": recording.duration_seconds,
                "dropped_frames": recording.dropped_frames,
                "decode_failures": recording.decode_failures,
            }

    def artifact_path(self, filename: str) -> Path | None:
        if filename not in self._ARTIFACT_TYPES:
            return None
        candidate = self._artifacts_dir / filename
        return candidate if candidate.is_file() else None

    def _artifact_output(self, filename: str) -> Path:
        if filename not in self._ARTIFACT_TYPES:
            raise ValueError(f"unsupported artifact: {filename}")
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        return self._artifacts_dir / filename

    def _device_status(self) -> dict[str, object]:
        try:
            status = dict(self._device_status_provider())
        except Exception:
            status = {}
        return {
            **status,
            "online": status.get("online") is True,
            "state": str(status.get("state") or "unavailable"),
            "last_error": status.get("last_error"),
        }

    def _ensure_device_online(self) -> None:
        if self._device_status().get("online") is not True:
            raise MediaLabDeviceOfflineError("Watcher device is offline")

    def _refresh_device_snapshot(self, connection: Mapping[str, object]) -> None:
        if connection.get("online") is not True:
            with self._state_lock:
                self._refreshed_connection_token = None
            return
        connection_token = str(connection.get("request_id") or "online")
        with self._state_lock:
            already_refreshed = (
                self._refreshed_connection_token == connection_token
                and bool(self._robot.capabilities)
            )
        if already_refreshed or not self._device_refresh_lock.acquire(blocking=False):
            return
        try:
            self._robot.refresh_device_info(timeout=1.0)
        except Exception:
            return
        finally:
            self._device_refresh_lock.release()
        with self._state_lock:
            self._refreshed_connection_token = connection_token

    @contextmanager
    def _operation(self, action: str) -> Iterator[None]:
        if not self._operation_lock.acquire(blocking=False):
            with self._state_lock:
                active_action = self._active_action or "another action"
            raise MediaLabBusyError(f"media lab is busy with {active_action}")
        try:
            self._ensure_device_online()
        except Exception:
            self._operation_lock.release()
            raise
        with self._state_lock:
            self._active_action = action
        self._append_event(action, f"{_action_label(action)} started", "running")
        try:
            yield
        except Exception as error:
            self._append_event(action, f"{_action_label(action)} failed: {error}", "error")
            raise
        else:
            self._append_event(action, f"{_action_label(action)} completed", "ok")
        finally:
            with self._state_lock:
                self._active_action = None
            self._operation_lock.release()

    def _append_event(self, action: str, message: str, tone: str) -> None:
        with self._state_lock:
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


def create_web_app(service: MediaLabService, *, web_root: Path) -> FastAPI:
    """Create the loopback-only HTTP surface used by the browser dashboard."""

    web_root = Path(web_root)
    app = FastAPI(
        title="WatcheRobot SDK Media Lab",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def local_security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(MediaLabBusyError)
    async def busy_handler(_request: Request, error: MediaLabBusyError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": "busy", "message": str(error)},
        )

    @app.exception_handler(MediaLabDeviceOfflineError)
    async def offline_handler(
        _request: Request,
        error: MediaLabDeviceOfflineError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": "device_offline", "message": str(error)},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_request",
                "message": "request fields are invalid",
            },
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(web_root.joinpath("index.html").read_text(encoding="utf-8"))

    @app.get("/assets/app.js")
    async def javascript() -> FileResponse:
        return FileResponse(web_root / "app.js", media_type="text/javascript")

    @app.get("/assets/styles.css")
    async def stylesheet() -> FileResponse:
        return FileResponse(web_root / "styles.css", media_type="text/css")

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        return await asyncio.to_thread(service.status)

    @app.get("/api/events")
    async def events(after: int = 0) -> dict[str, object]:
        return {"events": service.events(after=max(0, after))}

    @app.post("/api/actions/play-audio")
    async def play_audio() -> dict[str, object]:
        return await _run_action(service.play_audio)

    @app.post("/api/actions/stop-audio")
    async def stop_audio() -> dict[str, object]:
        return await _run_action(service.stop_audio)

    @app.post("/api/actions/capture-photo")
    async def capture_photo() -> dict[str, object]:
        result = await _run_action(service.capture_photo)
        result["artifact_url"] = _artifact_url(str(result["artifact"]))
        return result

    @app.post("/api/actions/record-microphone")
    async def record_microphone(request: RecordMicrophoneRequest) -> dict[str, object]:
        result = await _run_action(
            service.record_microphone,
            duration=request.duration,
        )
        result["artifact_url"] = _artifact_url(str(result["artifact"]))
        return result

    @app.get("/artifacts/{filename}")
    async def artifact(filename: str) -> FileResponse:
        path = service.artifact_path(filename)
        if path is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(path, media_type=MediaLabService._ARTIFACT_TYPES[filename])

    return app


async def _run_action(callback: Any, **kwargs: object) -> dict[str, object]:
    try:
        return await asyncio.to_thread(callback, **kwargs)
    except (MediaLabBusyError, MediaLabDeviceOfflineError, HTTPException):
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _artifact_url(filename: str) -> str:
    return f"/artifacts/{filename}?v={time.time_ns()}"


def _action_label(action: str) -> str:
    return action.replace("_", " ").title()


__all__ = [
    "DaemonDeviceStatusProvider",
    "MediaLabBusyError",
    "MediaLabDeviceOfflineError",
    "MediaLabService",
    "create_web_app",
]
