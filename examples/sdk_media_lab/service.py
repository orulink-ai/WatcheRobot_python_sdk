"""Local-only HTTP service for the standalone SDK Test Bench Application."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import math
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import wave
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from watcherobot.application import rtc as application_rtc


RTC_AUDIO_CAPABILITY = getattr(
    application_rtc,
    "RTC_AUDIO_CAPABILITY",
    "rtc.audio.full_duplex.v1",
)
RTC_VIDEO_CAPABILITY = application_rtc.RTC_VIDEO_CAPABILITY


_MDNS_HOST_CANDIDATE = re.compile(
    r"(?im)(^(?:a=)?candidate:\S+\s+\d+\s+\S+\s+\d+\s+)(\S+\.local)(\s+\d+\s+typ\s+host\b)"
)
_MAINTENANCE_INTERVAL_SECONDS = 0.25
_LOGGER = logging.getLogger(__name__)


class MediaLabBusyError(RuntimeError):
    """Raised when a second hardware action overlaps the active action."""


class MediaLabDeviceOfflineError(RuntimeError):
    """Raised when a hardware action is attempted without an online Watcher."""


class MediaLabCapabilityError(RuntimeError):
    """Raised when firmware does not advertise a requested public SDK domain."""

    def __init__(self, capability: str) -> None:
        super().__init__(f"Robot firmware does not advertise required capability: {capability}")
        self.capability = capability


class MediaLabPairingError(RuntimeError):
    """Expose a safe, stable Daemon pairing failure to the local dashboard."""

    def __init__(self, code: str, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class MediaLabRtcError(RuntimeError):
    """Expose a stable live-video failure to the local dashboard."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DaemonDeviceStatusProvider:
    """Read and manage the Daemon's loopback-only device slot."""

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

    def pair(
        self,
        pairing_code: str,
        device_ip: str | None = None,
    ) -> Mapping[str, object]:
        request_payload: dict[str, object] = {
            "pairing_code": pairing_code,
            "target_mode": "python_sdk",
        }
        if device_ip is not None:
            request_payload["device_ip"] = device_ip
        payload = json.dumps(request_payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._url.rstrip('/')}/pair",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                error_payload = json.loads(error.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_payload = {}
            code = str(error_payload.get("error") or "pairing_rejected")
            message = str(error_payload.get("message") or code)
            status_code = 409 if error.code == 409 else 422 if error.code < 500 else 502
            raise MediaLabPairingError(
                code,
                message,
                status_code=status_code,
            ) from error
        except Exception as error:
            raise MediaLabPairingError(
                "pairing_unavailable",
                "Daemon pairing request is unavailable",
            ) from error

        device = result.get("device") if isinstance(result, dict) else None
        if not isinstance(device, dict):
            raise MediaLabPairingError(
                "pairing_response_invalid",
                "Daemon pairing response is malformed",
            )
        return result


class RecordMicrophoneRequest(BaseModel):
    duration: float = Field(default=5.0, gt=0.0, le=30.0, allow_inf_nan=False)


class MotionMoveRequest(BaseModel):
    pan_deg: int = Field(ge=30, le=150)
    tilt_deg: int = Field(ge=100, le=130)
    duration_ms: int = Field(default=600, ge=100, le=5000)


class LightColorRequest(BaseModel):
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    brightness: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)
    zone: str = Field(default="all", pattern=r"^(all|side|bottom)$")


class LightEffectRequest(LightColorRequest):
    effect: str = Field(pattern=r"^(blink|breathing|rainbow|status_pulse)$")
    period_ms: int = Field(default=800, ge=100, le=5000)


class PairDeviceRequest(BaseModel):
    pairing_code: str = Field(pattern=r"^[0-9]{6}$")
    device_ip: str | None = None


class RtcSessionStartRequest(BaseModel):
    mode: str = Field(default="video", pattern=r"^(video|audio|av)$")


class RtcSignalRequest(BaseModel):
    kind: str = Field(pattern=r"^(offer|candidate)$")
    sdp: str | None = Field(default=None, max_length=16384)
    candidate: str | None = Field(default=None, max_length=2048)
    sdp_mid: str | None = Field(default=None, max_length=64)
    sdp_mline_index: int | None = Field(default=None, ge=0, le=65535)


class RtcClockPingRequest(BaseModel):
    browser_send_us: int = Field(gt=0, le=9_007_199_254_740_991)


class RtcFeedbackRequest(BaseModel):
    display_fps_x100: int = Field(ge=0)
    frame_age_p95_us: int = Field(ge=0)
    rtt_us: int = Field(ge=0)
    audio_queue_ms: int = Field(ge=0)
    audio_packet_loss_x100: int = Field(ge=0)
    audio_jitter_us: int = Field(ge=0)
    audio_concealed_frames: int = Field(ge=0)
    congestion_level: int = Field(ge=0, le=3)


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
        rtc: Any,
        artifacts_dir: Path,
        sample_audio: Path,
        device_status_provider: Callable[[], Mapping[str, object]],
        device_pairer: Callable[[str, str | None], Mapping[str, object]],
    ) -> None:
        self._robot = robot
        self._rtc = rtc
        self._artifacts_dir = Path(artifacts_dir)
        self._sample_audio = Path(sample_audio)
        self._device_status_provider = device_status_provider
        self._device_pairer = device_pairer
        # The lifecycle lock makes RTC start/stop/reconciliation atomic. The
        # operation lock excludes every other media action for the full RTC session;
        # the boolean records whether that session currently owns it.
        self._live_video_lifecycle_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_action: str | None = None
        self._event_sequence = 0
        self._events: deque[dict[str, object]] = deque(maxlen=160)
        self._device_refresh_lock = threading.Lock()
        self._refreshed_connection_token: str | None = None
        self._live_video_lock_held = False
        self._browser_host_ipv4: str | None = None
        self._append_event("system", "SDK Test Bench ready", "ok")

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
        rtc = self._rtc.snapshot()
        return {
            "connected": connection.get("online") is True,
            "connection": connection,
            "busy": active_action is not None,
            "active_action": active_action,
            "capabilities": list(self._robot.capabilities),
            "device": dict(self._robot.device_info),
            "resources": {
                "baseline": dict(self._robot.resource_baseline),
                "rtc_baseline": dict(self._robot.resource_rtc_baseline),
                "current": dict(self._robot.resource_snapshot),
                "history": list(self._robot.resource_history),
            },
            "rtc": rtc,
            "artifacts": artifacts,
            "events": self.events(),
        }

    def maintain(self) -> None:
        """Reconcile device and RTC lifecycle outside HTTP status requests."""

        with self._live_video_lifecycle_lock:
            connection = self._device_status()
            rtc = self._rtc.snapshot()
            if connection.get("online") is not True and rtc.get("active") is True:
                self._rtc.reset(reason="device_offline")
                rtc = self._rtc.snapshot()
            if connection.get("online") is not True or rtc.get("state") in {"stopped", "failed"}:
                self._release_live_video_lock()
        self._refresh_device_snapshot(connection)

    def events(self, *, after: int = 0) -> list[dict[str, object]]:
        with self._state_lock:
            return [
                dict(event)
                for event in self._events
                if isinstance((event_id := event.get("id")), int)
                and event_id > after
            ]

    def pair_device(
        self,
        pairing_code: str,
        device_ip: str | None = None,
    ) -> dict[str, object]:
        if (
            not isinstance(pairing_code, str)
            or len(pairing_code) != 6
            or any(character < "0" or character > "9" for character in pairing_code)
        ):
            raise ValueError("pairing code must be exactly 6 digits")
        if device_ip is not None:
            try:
                parsed_device_ip = ipaddress.IPv4Address(device_ip)
            except ipaddress.AddressValueError as error:
                raise ValueError("device IP must be a valid IPv4 address") from error
            device_ip = str(parsed_device_ip)
        self._append_event(
            "device_pairing",
            "Device pairing started",
            "running",
        )
        try:
            result = dict(self._device_pairer(pairing_code, device_ip))
            connection = result.get("device")
            if not isinstance(connection, dict):
                raise MediaLabPairingError(
                    "pairing_response_invalid",
                    "Daemon pairing response is malformed",
                )
        except Exception as error:
            self._append_event(
                "device_pairing",
                f"Device pairing failed: {error}",
                "error",
            )
            raise
        return {
            "accepted": True,
            "connection": dict(connection),
        }

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

    def move_motion(self, *, pan_deg: int, tilt_deg: int, duration_ms: int) -> dict[str, object]:
        with self._operation("motion_move"):
            self._ensure_capability("motion")
            job = self._robot.motion.move_to(
                pan_deg=pan_deg,
                tilt_deg=tilt_deg,
                duration_ms=duration_ms,
                profile="ease_in_out",
            )
            job.wait(duration_ms / 1000.0 + 2.0)
            return {
                "completed": True,
                "operation_id": job.id,
                "pan_deg": pan_deg,
                "tilt_deg": tilt_deg,
            }

    def stop_motion(self) -> dict[str, object]:
        self._ensure_device_online()
        self._ensure_capability("motion")
        self._robot.motion.stop()
        self._append_event("motion_stop", "Motion stop requested", "ok")
        return {"stopped": True}

    def set_light_color(self, *, color: str, brightness: float, zone: str) -> dict[str, object]:
        with self._operation("light_color"):
            self._ensure_capability("light")
            self._robot.lights.set_color(color, brightness=brightness, zone=zone)
            return {"applied": True}

    def play_light_effect(
        self,
        *,
        effect: str,
        color: str,
        brightness: float,
        zone: str,
        period_ms: int,
    ) -> dict[str, object]:
        with self._operation("light_effect"):
            self._ensure_capability("light")
            job = self._robot.lights.play_effect(
                effect,
                color=color,
                brightness=brightness,
                zone=zone,
                period_ms=period_ms,
                repeat=0,
            )
            return {"started": True, "operation_id": job.id}

    def turn_lights_off(self) -> dict[str, object]:
        self._ensure_device_online()
        self._ensure_capability("light")
        self._robot.lights.off()
        self._append_event("light_off", "Lights off requested", "ok")
        return {"off": True}

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

    def start_live_video(self, *, mode: str = "video") -> dict[str, object]:
        required_capabilities = {
            "audio": {RTC_AUDIO_CAPABILITY},
            "video": {RTC_VIDEO_CAPABILITY},
            "av": {RTC_AUDIO_CAPABILITY, RTC_VIDEO_CAPABILITY},
        }[mode]
        missing_capabilities = required_capabilities.difference(self._robot.capabilities)
        if missing_capabilities:
            feature = ", ".join(sorted(missing_capabilities))
            raise MediaLabRtcError(
                "rtc_unavailable",
                f"Robot firmware does not advertise required RTC capabilities: {feature}",
            )
        action = "rtc_audio" if mode == "audio" else "live_video"
        with self._live_video_lifecycle_lock:
            if not self._operation_lock.acquire(blocking=False):
                with self._state_lock:
                    active_action = self._active_action or "another action"
                raise MediaLabBusyError(f"media lab is busy with {active_action}")
            # Live video owns the media-operation lock for the whole RTC session. The
            # stop, terminal-event and device-offline paths all release it explicitly.
            with self._state_lock:
                self._live_video_lock_held = True
                self._active_action = action
            try:
                self._ensure_device_online()
                self._browser_host_ipv4 = self._resolve_browser_host_ipv4()
                session = dict(self._rtc.start(mode=mode))
            except Exception:
                self._release_live_video_lock()
                raise
        self._append_event(action, f"{_action_label(action)} session started", "running")
        return {"session": session}

    def send_rtc_signal(self, request: RtcSignalRequest) -> dict[str, object]:
        self._ensure_live_video_active()
        if request.kind == "offer":
            if not request.sdp:
                raise ValueError("offer signal requires sdp")
            self._rtc.send_offer(self._normalize_browser_candidates(request.sdp))
        else:
            if not request.candidate or request.sdp_mid is None or request.sdp_mline_index is None:
                raise ValueError("candidate signal requires candidate, sdp_mid, and sdp_mline_index")
            self._rtc.send_candidate(
                self._normalize_browser_candidates(request.candidate),
                sdp_mid=request.sdp_mid,
                sdp_mline_index=request.sdp_mline_index,
            )
        return {"accepted": True}

    def send_rtc_clock_ping(self, browser_send_us: int) -> dict[str, object]:
        self._ensure_live_video_active()
        self._rtc.clock_ping(browser_send_us)
        return {"accepted": True}

    def send_rtc_feedback(self, request: RtcFeedbackRequest) -> dict[str, object]:
        self._ensure_live_video_active()
        self._rtc.feedback(**request.model_dump())
        return {"accepted": True}

    def stop_live_video(self) -> dict[str, object]:
        with self._live_video_lifecycle_lock:
            with self._state_lock:
                action = (
                    self._active_action
                    if self._active_action in {"live_video", "rtc_audio"}
                    else "live_video"
                )
            stopped = bool(self._rtc.stop())
            self._release_live_video_lock()
        self._append_event(action, f"{_action_label(action)} session stopped", "ok")
        return {"stopped": stopped}

    def rtc_events(self, *, after: int = 0) -> list[dict[str, object]]:
        return self._rtc.events(after=max(0, after))

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

    def _ensure_capability(self, capability: str) -> None:
        if capability not in self._robot.capabilities:
            raise MediaLabCapabilityError(capability)

    def _ensure_live_video_active(self) -> None:
        self._ensure_device_online()
        with self._state_lock:
            active = self._live_video_lock_held and self._active_action in {
                "live_video",
                "rtc_audio",
            }
        if not active:
            raise MediaLabRtcError("rtc_not_active", "RTC session is not active")

    def _normalize_browser_candidates(self, value: str) -> str:
        if _MDNS_HOST_CANDIDATE.search(value) is None:
            return value
        replacement = self._browser_host_ipv4
        if replacement is None:
            raise MediaLabRtcError(
                "rtc_local_address_unavailable",
                "Unable to resolve the browser host LAN address for RTC",
            )
        return _rewrite_mdns_host_candidates(value, replacement)

    def _resolve_browser_host_ipv4(self) -> str | None:
        status = self._device_status()
        preview_url = status.get("preview_websocket_url")
        if not isinstance(preview_url, str) or not preview_url:
            return None
        peer_host = urllib.parse.urlparse(preview_url).hostname
        if not peer_host:
            return None
        try:
            peer_ip = ipaddress.IPv4Address(peer_host)
        except ipaddress.AddressValueError:
            return None
        route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            route.connect((str(peer_ip), 9))
            local_ip = ipaddress.IPv4Address(route.getsockname()[0])
        except (OSError, ipaddress.AddressValueError):
            return None
        finally:
            route.close()
        return None if local_ip.is_loopback or local_ip.is_unspecified else str(local_ip)

    def _release_live_video_lock(self) -> None:
        with self._state_lock:
            if not self._live_video_lock_held:
                return
            self._live_video_lock_held = False
            if self._active_action in {"live_video", "rtc_audio"}:
                self._active_action = None
        self._operation_lock.release()

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

    async def maintain_service() -> None:
        while True:
            try:
                await asyncio.to_thread(service.maintain)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("SDK Test Bench maintenance failed")
            await asyncio.sleep(_MAINTENANCE_INTERVAL_SECONDS)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        maintenance_task = asyncio.create_task(
            maintain_service(),
            name="sdk-media-lab-maintenance",
        )
        try:
            yield
        finally:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="WatcheRobot SDK Test Bench",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
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

    @app.exception_handler(MediaLabPairingError)
    async def pairing_handler(
        _request: Request,
        error: MediaLabPairingError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": error.code, "message": str(error)},
        )

    @app.exception_handler(MediaLabRtcError)
    async def rtc_handler(_request: Request, error: MediaLabRtcError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": error.code, "message": str(error)},
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

    @app.get("/assets/{module_name}.mjs")
    async def browser_module(module_name: str) -> FileResponse:
        if re.fullmatch(r"[a-z0-9-]+", module_name) is None:
            raise HTTPException(status_code=404, detail="Browser module not found")
        module_path = web_root / f"{module_name}.mjs"
        if not module_path.is_file():
            raise HTTPException(status_code=404, detail="Browser module not found")
        return FileResponse(module_path, media_type="text/javascript")

    @app.exception_handler(MediaLabCapabilityError)
    async def capability_handler(
        _request: Request,
        error: MediaLabCapabilityError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": "capability_unavailable",
                "message": str(error),
                "capability": error.capability,
            },
        )

    @app.get("/assets/styles.css")
    async def stylesheet() -> FileResponse:
        return FileResponse(web_root / "styles.css", media_type="text/css")

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        return await asyncio.to_thread(service.status)

    @app.get("/api/events")
    async def events(after: int = 0) -> dict[str, object]:
        return {"events": service.events(after=max(0, after))}

    @app.post("/api/device/pair", status_code=202)
    async def pair_device(request: PairDeviceRequest) -> dict[str, object]:
        return await _run_action(
            service.pair_device,
            pairing_code=request.pairing_code,
            device_ip=request.device_ip,
        )

    @app.post("/api/actions/play-audio")
    async def play_audio() -> dict[str, object]:
        return await _run_action(service.play_audio)

    @app.post("/api/actions/stop-audio")
    async def stop_audio() -> dict[str, object]:
        return await _run_action(service.stop_audio)

    @app.post("/api/controls/motion/move")
    async def move_motion(request: MotionMoveRequest) -> dict[str, object]:
        return await _run_action(
            service.move_motion,
            pan_deg=request.pan_deg,
            tilt_deg=request.tilt_deg,
            duration_ms=request.duration_ms,
        )

    @app.post("/api/controls/motion/stop")
    async def stop_motion() -> dict[str, object]:
        return await _run_action(service.stop_motion)

    @app.post("/api/controls/lights/color")
    async def set_light_color(request: LightColorRequest) -> dict[str, object]:
        return await _run_action(
            service.set_light_color,
            color=request.color,
            brightness=request.brightness,
            zone=request.zone,
        )

    @app.post("/api/controls/lights/effect")
    async def play_light_effect(request: LightEffectRequest) -> dict[str, object]:
        return await _run_action(
            service.play_light_effect,
            effect=request.effect,
            color=request.color,
            brightness=request.brightness,
            zone=request.zone,
            period_ms=request.period_ms,
        )

    @app.post("/api/controls/lights/off")
    async def turn_lights_off() -> dict[str, object]:
        return await _run_action(service.turn_lights_off)

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

    @app.post("/api/rtc/session/start")
    @app.post("/api/video/session/start")
    async def start_video(request: RtcSessionStartRequest) -> dict[str, object]:
        return await _run_action(service.start_live_video, mode=request.mode)

    @app.post("/api/rtc/session/signal")
    @app.post("/api/video/session/signal")
    async def video_signal(request: RtcSignalRequest) -> dict[str, object]:
        return await _run_action(service.send_rtc_signal, request=request)

    @app.post("/api/rtc/session/clock-ping")
    @app.post("/api/video/session/clock-ping")
    async def video_clock_ping(request: RtcClockPingRequest) -> dict[str, object]:
        return await _run_action(
            service.send_rtc_clock_ping,
            browser_send_us=request.browser_send_us,
        )

    @app.post("/api/rtc/session/feedback")
    @app.post("/api/video/session/feedback")
    async def video_feedback(request: RtcFeedbackRequest) -> dict[str, object]:
        return await _run_action(service.send_rtc_feedback, request=request)

    @app.get("/api/rtc/session/events")
    @app.get("/api/video/session/events")
    async def video_events(after: int = 0) -> dict[str, object]:
        return {"events": service.rtc_events(after=max(0, after))}

    @app.post("/api/rtc/session/stop")
    @app.post("/api/video/session/stop")
    async def stop_video() -> dict[str, object]:
        return await _run_action(service.stop_live_video)

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
    except (
        MediaLabBusyError,
        MediaLabCapabilityError,
        MediaLabDeviceOfflineError,
        MediaLabPairingError,
        MediaLabRtcError,
        HTTPException,
    ):
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _artifact_url(filename: str) -> str:
    return f"/artifacts/{filename}?v={time.time_ns()}"


def _rewrite_mdns_host_candidates(value: str, replacement_ip: str) -> str:
    ipaddress.IPv4Address(replacement_ip)
    return _MDNS_HOST_CANDIDATE.sub(
        lambda match: f"{match.group(1)}{replacement_ip}{match.group(3)}",
        value,
    )


def _action_label(action: str) -> str:
    return action.replace("_", " ").title()


__all__ = [
    "DaemonDeviceStatusProvider",
    "MediaLabBusyError",
    "MediaLabCapabilityError",
    "MediaLabDeviceOfflineError",
    "MediaLabPairingError",
    "MediaLabRtcError",
    "MediaLabService",
    "create_web_app",
]
