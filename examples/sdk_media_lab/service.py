"""Local-only HTTP service for the standalone SDK Test Bench Application."""

from __future__ import annotations

import asyncio
import base64
import copy
import ipaddress
import io
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
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from PIL import Image
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
_SCENARIO_MAX_SAMPLES = 3600
_SCENARIO_SAMPLE_INTERVAL_SECONDS = 1.0
_DIAGNOSTIC_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_DIAGNOSTIC_ARTIFACT = re.compile(
    rf"rtc-diagnostic-{_DIAGNOSTIC_ID}-(?:(?:computer|robot-raw|robot-clean)\.webm|(?:report|manifest)\.json)"
)
_TELEMETRY_STALE_SECONDS = 5.0
_PROCEDURAL_CAPABILITY = "expression.audio_follow.v1"
_SD_BASELINE_BEHAVIOR_ID = "desktop_expression_panel"
_SD_BASELINE_ANIMATION_ID = "standby"
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

    def __init__(self, code: str, message: str, *, owner: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.owner = owner


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


class ConcurrencyTestRequest(BaseModel):
    duration: float = Field(default=30.0, ge=1.0, le=120.0, allow_inf_nan=False)


def _concurrency_expression(frame: int) -> dict[str, Any]:
    """The four lightweight eye frames from the original concurrent bench."""
    frames = (
        (-0.65, -0.25, 0.72, -12, "#A1F03C"),
        (0.0, 0.25, 1.0, 0, "#42D9FF"),
        (0.65, -0.10, 0.82, 12, "#FFB43C"),
        (0.0, 0.0, 0.92, 0, "#C38BFF"),
    )
    gaze_x, gaze_y, openness, tilt_deg, color = frames[frame % len(frames)]
    return dict(gaze_x=gaze_x, gaze_y=gaze_y, openness=openness,
                tilt_deg=tilt_deg, color=color, transition_ms=120)


def _concurrency_results(report: dict[str, Any]) -> dict[str, Any]:
    """Keep per-channel evidence separate from the overall workload verdict."""
    results = {}
    evidence = {
        "camera": "JPEG decoded and saved",
        "speaker": "SDK playback job completed",
        "ui": "Device acknowledged UI commands",
    }
    for name in ("camera", "speaker", "ui"):
        operations = [op for op in report["operations"] if op["worker"] == name]
        succeeded = sum(op["success"] for op in operations)
        failures = [op for op in operations if not op["success"] and not op.get("interrupted")]
        updates = sum(op["success"] and op["stage"] == "update" for op in operations)
        cleanup = [item["error"] for item in report["errors"] if item["worker"] == f"{name}_cleanup"]
        status = (
            "failed" if failures else
            "running" if report.get("running") and (name != "speaker" or not succeeded) else
            "not_started" if not operations else
            "cleanup_failed" if cleanup else
            "interrupted" if report["cancelled"] or report["errors"] else
            "incomplete" if name == "ui" and not updates else "passed"
        )
        latencies = [(op["end_s"] - op["start_s"]) * 1000 for op in operations]
        results[name] = {
            "status": status, "attempted": len(operations), "succeeded": succeeded,
            "failed": len(failures),
            "average_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "max_ms": round(max(latencies), 2) if latencies else None,
            "last_error": failures[-1]["error"] if failures else None,
            "cleanup_error": cleanup[-1] if cleanup else None,
            "evidence": evidence[name] if succeeded else "No successful operation",
            "physical_confirmation": "not_verified" if name != "camera" else "jpeg_validated" if succeeded else "not_verified",
        }
        if name == "ui":
            results[name]["updates_succeeded"] = updates
            results[name]["startup_succeeded"] = any(op["success"] and op["stage"] == "start" for op in operations)
        if name == "camera":
            images = [op["image"] for op in operations if op["success"]]
            results[name]["total_bytes"] = sum(item["bytes"] for item in images)
            results[name]["last_image"] = images[-1] if images else None
    return results


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


class AnimationPlayRequest(BaseModel):
    animation_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")


class PairDeviceRequest(BaseModel):
    pairing_code: str = Field(pattern=r"^[0-9]{6}$")
    device_ip: str | None = None


class InferenceStartRequest(BaseModel):
    preview: bool = Field(default=False, strict=True)
    model_id: int = Field(ge=1, le=255, strict=True)


class RtcSessionStartRequest(BaseModel):
    mode: str = Field(default="video", pattern=r"^(video|audio|av)$")
    request_id: str | None = Field(default=None, min_length=8, max_length=63, pattern=r"^[A-Za-z0-9._:-]+$")


class RtcSessionStopRequest(BaseModel):
    request_id: str | None = Field(default=None, min_length=8, max_length=63, pattern=r"^[A-Za-z0-9._:-]+$")


class ScenarioRecordingStartRequest(BaseModel):
    label: str = Field(default="combined-scene", min_length=1, max_length=120)


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
    """Arbitrate hardware resources and expose stable, JSON-ready diagnostics."""

    _ARTIFACT_TYPES = {
        "camera.jpg": "image/jpeg",
        "camera-feedback.jpg": "image/jpeg",
        "microphone.wav": "audio/wav",
        "rtc-diagnostic-latest.json": "application/json",
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
        self._inference_lock = threading.RLock()
        self._inference_session = None
        self._inference_lease = None
        self._inference_state = "idle"
        self._robot = robot
        self._concurrency_cancel = threading.Event()
        self._concurrency_report: dict[str, Any] | None = None
        self._concurrency_shutdown = threading.Event()
        self._concurrency_lifecycle_lock = threading.Lock()
        self._concurrency_pending_cleanup: dict[str, str] = {}
        self._rtc = rtc
        self._artifacts_dir = Path(artifacts_dir)
        self._sample_audio = Path(sample_audio)
        self._device_status_provider = device_status_provider
        self._device_pairer = device_pairer
        # RTC lifecycle transitions remain atomic while camera, microphone, and
        # speaker ownership are tracked independently. This permits the verified
        # audio-RTC + photo and video-RTC + standalone-audio combinations without
        # weakening same-hardware exclusion.
        self._live_video_lifecycle_lock = threading.Lock()
        self._resource_locks = {
            "camera": threading.Lock(),
            "microphone": threading.Lock(),
            "speaker": threading.Lock(),
            "motion": threading.Lock(),
            "light": threading.Lock(),
            "animation": threading.Lock(),
        }
        self._state_lock = threading.Lock()
        self._active_action: str | None = None
        self._active_actions: dict[str, str] = {}
        self._event_sequence = 0
        self._events: deque[dict[str, object]] = deque(maxlen=160)
        self._device_refresh_lock = threading.Lock()
        self._refreshed_connection_token: str | None = None
        self._live_video_lock_held = False
        self._rtc_resources_held: tuple[str, ...] = ()
        self._rtc_request_id: str | None = None
        self._browser_host_ipv4: str | None = None
        self._face_lock = threading.RLock()
        self._face_lease: Any = None
        self._face_state = "idle"
        self._face_preview: Any = None
        self._face_preview_last_frame_at = 0.0
        self._procedural_lock = threading.RLock()
        self._procedural_lease: Any = None
        self._procedural_state = "idle"
        self._procedural_captures = 0
        self._sd_baseline_lock = threading.RLock()
        self._sd_baseline_lease: Any = None
        self._sd_baseline_state = "idle"
        self._sd_baseline_operation_id: int | None = None
        self._recording_lock = threading.RLock()
        self._recording_generation = 0
        self._diagnostic_lock = threading.Lock()
        self._recording: dict[str, Any] = {"active": False, "sample_count": 0}
        self._recording_samples: deque[dict[str, object]] = deque(maxlen=_SCENARIO_MAX_SAMPLES)
        self._recording_last_sample_at: float | None = None
        self._recording_started_monotonic = 0.0
        self._telemetry_identity: tuple[object, ...] | None = None
        self._telemetry_updated_at: float | None = None
        self._telemetry_lock = threading.RLock()
        self._telemetry_context: tuple[object, ...] | None = None
        self._telemetry_waiting_for_frame = False
        self._recording_device: dict[str, object] = {}
        self._recording_capabilities: list[str] = []
        self._recording_baseline: dict[str, object] = {}
        self._append_event("system", "SDK Test Bench ready", "ok")

    def status(self) -> dict[str, object]:
        with self._state_lock:
            active_action = self._active_action
            active_actions = dict(self._active_actions)
            face_state = self._face_state
            concurrency_cleanup = dict(self._concurrency_pending_cleanup)
        inference_session = self._inference_session
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
            "busy": bool(active_actions),
            "active_action": active_action,
            "active_actions": list(active_actions.values()),
            "resource_owners": active_actions,
            "concurrency_cleanup": concurrency_cleanup,
            "capabilities": list(self._robot.capabilities),
            "animations": list(self._robot.animation.available_ids),
            "device": dict(self._robot.device_info),
            "resources": {
                "baseline": dict(self._robot.resource_baseline),
                "rtc_baseline": dict(self._robot.resource_rtc_baseline),
                "current": dict(self._robot.resource_snapshot),
                "history": list(self._robot.resource_history),
                "telemetry": self.resource_telemetry_status(connection=connection),
            },
            "rtc": rtc,
            "procedural": self.procedural_status(connection=connection),
            "sd_baseline": self.sd_baseline_status(),
            "scenario_recording": self.scenario_recording_status(),
            "inference": {"state": self._inference_state,
                          "session_id": inference_session.id if inference_session else None,
                          "model_id": inference_session.model_id if inference_session else None},
            "face_tracking": {
                "state": face_state,
                "supported": "face_tracking.control.v1" in self._robot.capabilities,
                "preview_supported": "face_tracking.preview.v1" in self._robot.capabilities,
                "preview_running": self._face_preview is not None and face_state == "running",
                "preview_receiving": self._face_preview is not None and
                    time.monotonic() - self._face_preview_last_frame_at < 2.0,
            },
            "artifacts": artifacts,
            "events": self.events(),
        }

    def maintain(self) -> None:
        """Reconcile device and RTC lifecycle outside HTTP status requests."""

        with self._live_video_lifecycle_lock:
            connection = self._device_status()
            rtc = self._rtc.snapshot()
            disconnected = connection.get("online") is False and connection.get("state") in {
                "idle", "disconnected", "discovering", "connecting", "reconnecting",
            }
            if disconnected and self._concurrency_lifecycle_lock.acquire(blocking=False):
                try:
                    for name in tuple(self._concurrency_pending_cleanup):
                        self._release_concurrency_cleanup(name)
                finally:
                    self._concurrency_lifecycle_lock.release()
            if connection.get("online") is not True and rtc.get("active") is True:
                self._rtc.reset(reason="device_offline")
                rtc = self._rtc.snapshot()
            # ``failed`` is diagnostic, not a release barrier: the Device follows it
            # with teardown and a terminal ``stopped`` event. Releasing the media
            # lease on ``failed`` would allow a new camera/audio action to overlap
            # the old session's hardware cleanup.
            if connection.get("online") is not True or rtc.get("state") == "stopped":
                self._release_live_video_lock()
        self._refresh_device_snapshot(connection)
        with self._inference_lock:
            if self._inference_lease is not None:
                if connection.get("online") is not True:
                    self._inference_state = "stop_required"
                elif self._inference_state == "stop_required":
                    self.stop_inference()
        with self._face_lock:
            if self._face_lease is not None:
                if connection.get("online") is not True:
                    self._set_face_state("stop_required")
                elif self._face_state == "stop_required":
                    self.stop_face_tracking()
        # A command thread may be waiting for an ACK. Do not queue maintenance
        # behind it: status uses short state locks and sampling must continue.
        if self._procedural_lock.acquire(blocking=False):
            try:
                if self._procedural_lease is not None:
                    if connection.get("online") is not True:
                        self._set_procedural_state("stop_required")
                    elif self._procedural_state == "stop_required":
                        self.stop_procedural()
            finally:
                self._procedural_lock.release()
        if self._sd_baseline_lock.acquire(blocking=False):
            try:
                if self._sd_baseline_lease is not None:
                    if connection.get("online") is not True:
                        self._set_sd_baseline_state("stop_required")
                    elif self._sd_baseline_state == "stop_required":
                        self.stop_sd_baseline()
            finally:
                self._sd_baseline_lock.release()
        self._sample_scenario(connection=connection)

    def _set_procedural_state(self, state: str) -> None:
        with self._state_lock:
            self._procedural_state = state

    def _set_sd_baseline_state(self, state: str) -> None:
        with self._state_lock:
            self._sd_baseline_state = state

    def sd_baseline_status(self) -> dict[str, object]:
        """Identify the fixed, silent SD loop used for comparison recordings."""
        with self._state_lock:
            return {
                "supported": (
                    "behavior" in self._robot.capabilities
                    and "animation" in self._robot.capabilities
                    and _SD_BASELINE_ANIMATION_ID in self._robot.animation.available_ids
                ),
                "state": self._sd_baseline_state,
                "behavior_id": _SD_BASELINE_BEHAVIOR_ID,
                "animation_id": _SD_BASELINE_ANIMATION_ID,
                "operation_id": self._sd_baseline_operation_id,
            }

    def start_sd_baseline(self) -> dict[str, object]:
        with self._sd_baseline_lock:
            self._ensure_device_online()
            self._ensure_capability("behavior")
            self._ensure_capability("animation")
            if _SD_BASELINE_ANIMATION_ID not in self._robot.animation.available_ids:
                raise ValueError("Fixed SD baseline requires the advertised standby animation")
            if self._sd_baseline_lease is not None:
                if self._sd_baseline_state == "running":
                    return self.sd_baseline_status()
                raise MediaLabBusyError("Confirm SD baseline stop before starting again")
            lease = self._operation("sd_baseline", resource="animation")
            lease.__enter__()
            self._sd_baseline_lease = lease
            self._set_sd_baseline_state("starting")
            try:
                # The firmware catalog defines a fixed standby loop with no
                # motion or sound. AnimationDomain.play is a one-shot contract.
                job = self._robot.behavior.play(_SD_BASELINE_BEHAVIOR_ID)
            except Exception:
                self._set_sd_baseline_state("stop_required")
                try:
                    self.stop_sd_baseline()
                except Exception:
                    _LOGGER.exception("SD baseline startup cleanup remains unconfirmed")
                raise
            self._sd_baseline_operation_id = job.id
            self._set_sd_baseline_state("running")
            return self.sd_baseline_status()

    def stop_sd_baseline(self) -> dict[str, object]:
        with self._sd_baseline_lock:
            if self._sd_baseline_lease is None:
                return self.sd_baseline_status()
            self._set_sd_baseline_state("stop_required")
            self._ensure_device_online()
            self._robot.behavior.stop()
            self._sd_baseline_lease.__exit__(None, None, None)
            self._sd_baseline_lease = None
            self._sd_baseline_operation_id = None
            self._set_sd_baseline_state("idle")
            return self.sd_baseline_status()

    def procedural_status(self, *, connection: Mapping[str, object] | None = None) -> dict[str, object]:
        """Expose device mouth evidence without substituting browser activity."""
        animation = self._robot.resource_snapshot.get("animation", {})
        if not isinstance(animation, Mapping):
            animation = {}
        with self._state_lock:
            state = self._procedural_state
        available = (
            isinstance(animation.get("audio_follow"), bool)
            and isinstance(animation.get("mouth_level_milli"), int)
            and isinstance(animation.get("pcm_frames"), int)
            and animation.get("source") == "rtc_playback"
            and self.resource_telemetry_status(connection=connection)["status"] == "available"
        )
        return {
            "supported": _PROCEDURAL_CAPABILITY in self._robot.capabilities,
            "state": state,
            "style": "radial",
            "mouth_source": "rtc_playback",
            "telemetry_available": available,
            "audio_follow": animation.get("audio_follow") if available else None,
            "mouth_level_milli": animation.get("mouth_level_milli") if available else None,
            "pcm_frames": animation.get("pcm_frames") if available else None,
            "design_id": animation.get("design_id") if available else None,
        }

    def diagnostic_speech_path(self) -> Path:
        """Expose only the configured bundled fixture, never a caller's path."""
        if not self._sample_audio.is_file():
            raise HTTPException(status_code=404, detail="RTC speech fixture not found")
        return self._sample_audio

    def start_procedural(self) -> dict[str, object]:
        with self._procedural_lock:
            self._ensure_device_online()
            self._ensure_capability(_PROCEDURAL_CAPABILITY)
            if self._procedural_lease is not None:
                if self._procedural_state == "running":
                    return {**self.procedural_status(), "started": False}
                raise MediaLabBusyError("Confirm procedural stop before starting again")
            lease = self._operation("procedural", resource="animation")
            lease.__enter__()
            self._procedural_lease = lease
            self._set_procedural_state("starting")
            try:
                self._robot.expression_runtime.set_audio_follow(True)
            except Exception:
                self._set_procedural_state("stop_required")
                try:
                    self.stop_procedural()
                except Exception:
                    _LOGGER.exception("Procedural startup cleanup remains unconfirmed")
                raise
            self._set_procedural_state("running")
            return {**self.procedural_status(), "started": True}

    def stop_procedural(self) -> dict[str, object]:
        with self._procedural_lock:
            if self._procedural_lease is None:
                return self.procedural_status()
            if self._procedural_captures:
                raise MediaLabBusyError("Wait for the in-flight photo before stopping procedural mode")
            self._set_procedural_state("stop_required")
            self._ensure_device_online()
            self._robot.expression_runtime.set_audio_follow(False)
            self._procedural_lease.__exit__(None, None, None)
            self._procedural_lease = None
            self._set_procedural_state("idle")
            return self.procedural_status()

    def scenario_recording_status(self) -> dict[str, object]:
        with self._recording_lock:
            return {key: value for key, value in self._recording.items() if key != "summary"}

    def start_scenario_recording(self, *, label: str = "combined-scene") -> dict[str, object]:
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 120:
            raise ValueError("label must contain 1 to 120 characters")
        with self._recording_lock:
            if self._recording.get("active") is True:
                raise MediaLabBusyError("Stop the current scenario recording first")
            self._recording_generation += 1
            self._recording = {
                "active": True, "label": label.strip(), "started_at": time.time(),
                "stopped_at": None, "sample_count": 0, "dropped_samples": 0,
                "max_samples": _SCENARIO_MAX_SAMPLES, "summary": {"memory": {}},
            }
            self._recording_samples = deque(maxlen=_SCENARIO_MAX_SAMPLES)
            self._recording_started_monotonic = time.monotonic()
            self._recording_last_sample_at = None
            self._recording_device = deepcopy(self._robot.device_info)
            self._recording_capabilities = list(self._robot.capabilities)
            self._recording_baseline = deepcopy(self._robot.resource_baseline)
        self._sample_scenario()
        self._append_event("scenario_recording", f"Recording started: {label.strip()}", "running")
        return self.scenario_recording_status()

    def stop_scenario_recording(self) -> dict[str, object]:
        with self._recording_lock:
            generation = self._recording_generation
        self._sample_scenario()
        with self._recording_lock:
            if generation == self._recording_generation and self._recording.get("active") is True:
                self._recording["active"] = False
                self._recording["stopped_at"] = time.time()
        return self.scenario_recording_status()

    def scenario_report(self) -> dict[str, object]:
        with self._recording_lock:
            report = deepcopy(self._recording)
            report["samples"] = deepcopy(list(self._recording_samples))
            report["device"] = deepcopy(self._recording_device)
            report["capabilities"] = list(self._recording_capabilities)
            report["baseline"] = deepcopy(self._recording_baseline)
        report.update(
            schema="watcher-media-lab-scene/1", exported_at=time.time(),
            events=self.events(),
        )
        return report

    def resource_telemetry_status(self, *, connection: Mapping[str, object] | None = None) -> dict[str, object]:
        """Age actual device events; a new connection does not refresh cached data."""
        connection = self._device_status() if connection is None else connection
        snapshot = self._robot.resource_snapshot
        received = getattr(self._robot, "resource_snapshot_received_at", None)
        now = time.monotonic()
        has_receipt = isinstance(received, (int, float)) and not isinstance(received, bool) and math.isfinite(received)
        marker = ("received", received) if has_receipt else ("snapshot", snapshot.get("sequence"), snapshot.get("captured_at_ms"))
        if not snapshot or (not has_receipt and all(value is None for value in marker[1:])):
            marker = None
        context = (self._robot.device_info.get("device_id"), connection.get("request_id"), connection.get("connection_id"))
        online = connection.get("online") is True
        with self._telemetry_lock:
            if not online or (self._telemetry_context is not None and self._telemetry_context != context):
                self._telemetry_waiting_for_frame = True
            if marker is not None and marker != self._telemetry_identity:
                self._telemetry_identity = marker
                self._telemetry_updated_at = min(float(received), now) if has_receipt else now
                if online:
                    self._telemetry_waiting_for_frame = False
            self._telemetry_context = context
            age = None if self._telemetry_updated_at is None else max(0.0, now - self._telemetry_updated_at)
            state = (
                "unavailable" if not online or marker is None or self._telemetry_waiting_for_frame
                else "stale" if age is None or age >= _TELEMETRY_STALE_SECONDS
                else "available"
            )
            return {"status": state, "age_seconds": age}

    def _sample_scenario(self, *, connection: Mapping[str, object] | None = None) -> None:
        with self._recording_lock:
            if self._recording.get("active") is not True:
                return
            generation = self._recording_generation
            now = time.monotonic()
            if self._recording_last_sample_at is not None and now - self._recording_last_sample_at < _SCENARIO_SAMPLE_INTERVAL_SECONDS:
                return
        connection = self._device_status() if connection is None else connection
        snapshot = deepcopy(self._robot.resource_snapshot)
        rtc = deepcopy(self._rtc.snapshot())
        procedural = self.procedural_status(connection=connection)
        sd_baseline = self.sd_baseline_status()
        telemetry = self.resource_telemetry_status(connection=connection)
        with self._state_lock:
            owners = dict(self._active_actions)
        now = time.monotonic()
        with self._recording_lock:
            if generation != self._recording_generation or self._recording.get("active") is not True:
                return
            if self._recording_last_sample_at is not None and now - self._recording_last_sample_at < _SCENARIO_SAMPLE_INTERVAL_SECONDS:
                return
            self._recording_last_sample_at = now
            sample = {
                "timestamp": time.time(), "elapsed_seconds": round(now - self._recording_started_monotonic, 3),
                "connected": connection.get("online") is True, "resource_owners": owners,
                "device_id": self._robot.device_info.get("device_id"),
                "connection": {key: connection.get(key) for key in ("request_id", "connection_id")},
                "telemetry": telemetry,
                "resources": snapshot, "rtc": rtc, "procedural": procedural,
                "sd_baseline": sd_baseline,
            }
            if len(self._recording_samples) == self._recording_samples.maxlen:
                self._recording["dropped_samples"] += 1
            self._recording_samples.append(sample)
            self._recording["sample_count"] += 1
            memory = snapshot.get("memory", {})
            if telemetry["status"] == "available" and isinstance(memory, Mapping):
                summary = self._recording["summary"]["memory"]
                for domain in ("internal", "dma", "psram"):
                    metrics = memory.get(domain)
                    if not isinstance(metrics, Mapping):
                        continue
                    for metric in ("free_bytes", "largest_free_block_bytes", "minimum_free_bytes"):
                        value = metrics.get(metric)
                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                            minima = summary.setdefault(domain, {})
                            key = f"{metric}_min"
                            minima[key] = min(minima.get(key, value), value)

    def vision_models(self) -> dict[str, object]:
        self._ensure_device_online()
        self._ensure_capability("vision.models.v1")
        return {"models": [asdict(model) for model in self._robot.vision.models()]}

    def start_inference(self, model_id: int, *, preview: bool = False) -> dict[str, object]:
        with self._inference_lock:
            self._ensure_device_online()
            self._ensure_capability("vision.inference.v1")
            if self._inference_lease is not None:
                raise MediaLabBusyError("Stop the current inference session first")
            lease = self._operation("vision_inference", resources=("camera",))
            lease.__enter__()
            self._inference_lease = lease
            self._inference_state = "starting"
            try:
                self._inference_session = self._robot.vision.start_inference(model_id, **({"preview": True} if preview else {}))
            except Exception:
                self._inference_state = "stop_required"
                try:
                    self.stop_inference()
                except Exception:
                    _LOGGER.exception("Inference startup cleanup remains unconfirmed")
                raise
            self._inference_state = "running"
            return {"state": "running", "session_id": self._inference_session.id, "model_id": model_id}

    def inference_result(self) -> dict[str, object]:
        with self._inference_lock:
            if self._inference_session is None or self._inference_state != "running":
                raise MediaLabBusyError("No running inference session")
            result = self._inference_session.latest(timeout=3.0)
            if result is None:
                return {"ready": False}
            data = asdict(result)
            jpeg = data.pop("jpeg", None)
            if jpeg is not None:
                data["jpeg_base64"] = base64.b64encode(jpeg).decode("ascii")
            return {"ready": True, **data}

    def stop_inference(self) -> dict[str, object]:
        with self._inference_lock:
            if self._inference_lease is None:
                return {"state": "idle"}
            self._inference_state = "stop_required"
            self._robot.vision.stop_inference()
            self._inference_lease.__exit__(None, None, None)
            self._inference_lease = None
            self._inference_session = None
            self._inference_state = "idle"
            return {"state": "idle"}

    def vision_status(self) -> dict[str, object]:
        self._ensure_device_online()
        self._ensure_capability("vision.status.v1")
        return asdict(self._robot.vision.status(timeout=3.0))

    def _set_face_state(self, state: str) -> None:
        with self._state_lock:
            self._face_state = state

    def start_face_tracking(self, *, preview: bool = False) -> dict[str, object]:
        with self._face_lock:
            self._ensure_device_online()
            self._ensure_capability("face_tracking.control.v1")
            if preview:
                self._ensure_capability("face_tracking.preview.v1")
            if self._face_lease is not None:
                if self._face_state != "running":
                    raise MediaLabBusyError("Face tracking stop must be confirmed first")
                if preview != (self._face_preview is not None):
                    raise MediaLabBusyError("Stop face tracking before changing preview mode")
                return {"state": self._face_state}
            lease = self._operation("face_tracking", resources=("camera", "motion"))
            lease.__enter__()
            self._face_lease = lease
            self._set_face_state("starting")
            try:
                # Cold startup includes validating the four on-device model slots.
                if preview:
                    self._face_preview_last_frame_at = 0.0
                    self._face_preview = self._robot.face_tracking.open_preview(
                        width=640, height=480, frame_stride=1, stop_policy="hold", queue_size=1,
                    )
                else:
                    self._robot.face_tracking.start(timeout=10.0)
            except Exception:
                # A timeout does not prove the motors never started.
                self._set_face_state("stop_required")
                try:
                    self.stop_face_tracking()
                except Exception:
                    _LOGGER.exception("Face tracking start cleanup remains unconfirmed")
                raise
            self._set_face_state("running")
            return {"state": "running"}

    def face_preview_frame(self) -> dict[str, object]:
        # One bounded read, no unbounded frame backlog or independent device connection.
        with self._face_lock:
            if self._face_preview is None or self._face_state != "running":
                raise MediaLabBusyError("No running face preview")
            try:
                frame = self._face_preview.read(timeout=0.05)
            except TimeoutError:
                return {"ready": False}
            self._face_preview_last_frame_at = time.monotonic()
            return {"ready": True, "sequence": frame.sequence, "width": frame.width,
                    "height": frame.height, "jpeg_base64": base64.b64encode(frame.jpeg).decode("ascii"),
                    "faces": [asdict(face) for face in frame.faces],
                    "telemetry": {field.name: getattr(frame.telemetry, field.name)
                                  for field in fields(frame.telemetry) if field.name not in {"raw", "faces"}}}

    def stop_face_tracking(self) -> dict[str, object]:
        with self._face_lock:
            if self._face_lease is None:
                return {"state": "idle"}
            self._set_face_state("stop_required")
            self._robot.face_tracking.stop(policy="hold", timeout=2.0)
            self._face_lease.__exit__(None, None, None)
            self._face_lease = None
            self._face_preview = None
            self._set_face_state("idle")
            return {"state": "idle"}

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
        with self._operation("play_audio", resources=("microphone", "speaker")):
            if not self._sample_audio.is_file():
                raise FileNotFoundError(f"sample audio is missing: {self._sample_audio}")
            playback = self._robot.audio.play_file(self._sample_audio)
            playback.wait(30.0)
            return {
                "source": self._sample_audio.name,
                "bytes": self._sample_audio.stat().st_size,
            }

    def stop_concurrency_test(self) -> dict[str, object]:
        self._concurrency_cancel.set()
        return {"stop_requested": True}

    def request_shutdown(self) -> None:
        """Signal workers before the HTTP server begins draining requests."""
        self._concurrency_shutdown.set()
        self.stop_concurrency_test()

    @staticmethod
    def _concurrency_cleanup_resources(name: str) -> tuple[str, ...]:
        return ("animation",) if name == "ui" else ("microphone", "speaker")

    def _release_concurrency_cleanup(self, name: str) -> None:
        # Caller owns the lifecycle lock; no test/retry can take over this lease.
        with self._state_lock:
            self._concurrency_pending_cleanup.pop(name, None)
            for resource in self._concurrency_cleanup_resources(name):
                if self._active_actions.get(resource) == "concurrency_cleanup":
                    self._active_actions.pop(resource)
                    self._resource_locks[resource].release()
            self._refresh_active_action_locked()

    def retry_concurrency_cleanup(self) -> dict[str, object]:
        if not self._concurrency_lifecycle_lock.acquire(blocking=False):
            raise MediaLabBusyError("concurrent test is still running")
        try:
            self._ensure_device_online()
            for name in tuple(self._concurrency_pending_cleanup):
                stop = self._robot.expression_runtime.stop if name == "ui" else self._robot.audio.stop
                try:
                    stop()
                except Exception as exc:
                    with self._state_lock:
                        self._concurrency_pending_cleanup[name] = str(exc) or type(exc).__name__
                else:
                    self._release_concurrency_cleanup(name)
            with self._state_lock:
                return {"pending": dict(self._concurrency_pending_cleanup)}
        finally:
            self._concurrency_lifecycle_lock.release()

    def _wait_concurrency_audio(self, playback: Any) -> bool:
        deadline = time.monotonic() + 30.0
        while not self._concurrency_cancel.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("audio playback did not finish before timeout")
            try:
                playback.wait(min(0.1, remaining))
                return True
            except TimeoutError:
                continue
        return False

    def run_concurrency_test(self, *, duration: float) -> dict[str, Any]:
        if self._concurrency_shutdown.is_set():
            raise MediaLabBusyError("application is shutting down")
        if not self._concurrency_lifecycle_lock.acquire(blocking=False):
            raise MediaLabBusyError("concurrent test or cleanup is still running")
        try:
            return self._run_concurrency_test(duration=duration)
        finally:
            self._concurrency_lifecycle_lock.release()

    def _run_concurrency_test(self, *, duration: float) -> dict[str, Any]:
        """Run bounded concurrent SDK calls inside one exclusive bench lease."""
        if isinstance(duration, bool) or not math.isfinite(duration) or not 1 <= duration <= 120:
            raise ValueError("duration must be between 1 and 120 seconds")
        for capability in ("expression.runtime.v3", "camera.capture", "audio.stream"):
            self._ensure_capability(capability)
        retained_resources: set[str] = set()
        with self._operation("concurrency_test", resources=("camera", "animation", "microphone", "speaker"), retain_resources=retained_resources):
            if not self._concurrency_shutdown.is_set():
                self._concurrency_cancel.clear()
            run_id = uuid.uuid4().hex[:12]
            self._artifacts_dir.mkdir(parents=True, exist_ok=True)
            started = time.monotonic()
            deadline = started + duration
            report: dict[str, Any] = {
                "run_id": run_id, "duration_requested": duration,
                "running": True, "started": False, "cancelled": False, "passed": False,
                "counts": {"camera": 0, "speaker": 0, "ui": 0},
                "operations": [], "errors": [], "samples": [],
                "report": f"concurrency-{run_id}.json",
                "report_saved": False, "report_save_error": None,
                "profile": {"strategy": "legacy_concurrent", "ui_interval_s": 0.18,
                            "camera_interval_s": 1.0, "audio_repetitions": 1},
                "baseline": dict(self._robot.resource_snapshot),
            }
            report_lock = threading.Lock()
            gate = threading.Event()
            startup_complete = threading.Event()
            cleanup_claimed: set[str] = set()

            def publish() -> None:
                # Readers receive an immutable snapshot, never a live worker list.
                with report_lock:
                    snapshot = copy.deepcopy(report)
                snapshot["elapsed_s"] = time.monotonic() - started
                snapshot["results"] = _concurrency_results(snapshot)
                self._concurrency_report = snapshot

            publish()

            def error(worker: str, exc: Exception) -> None:
                with report_lock:
                    report["errors"].append({"worker": worker, "error": str(exc) or type(exc).__name__})
                self._concurrency_cancel.set()

            def cleanup_once(name: str) -> None:
                if not startup_complete.is_set():
                    return  # No device work is allowed before all submissions succeed.
                # Claim under the report lock, but never hold it across device I/O.
                with report_lock:
                    if name in cleanup_claimed:
                        return
                    cleanup_claimed.add(name)
                stop = self._robot.audio.stop if name == "speaker" else self._robot.expression_runtime.stop
                try:
                    stop()
                except Exception as exc:
                    error(f"{name}_cleanup", exc)
                    with report_lock:
                        retained_resources.update(self._concurrency_cleanup_resources(name))
                    with self._state_lock:
                        self._concurrency_pending_cleanup[name] = str(exc) or type(exc).__name__

            def run_worker(name: str) -> None:
                gate.wait()
                if not startup_complete.is_set():
                    return
                # All three initial requests are scheduled together. A failure
                # cancels repeats, not another channel's initial measurement.
                first_attempt = True
                while first_attempt or (not self._concurrency_cancel.is_set() and time.monotonic() < deadline):
                    if self._concurrency_shutdown.is_set():
                        break
                    tick = time.monotonic()
                    stage = ("start" if first_attempt else "update") if name == "ui" else "run"
                    operation: dict[str, Any] = {"worker": name, "stage": stage, "start_s": tick - started, "success": False}
                    try:
                        if name == "ui":
                            if first_attempt:
                                self._robot.expression_runtime.start(
                                    preset="thinking", style="watcher_pulse", color="#A1F03C",
                                    gaze_x=0.0, gaze_y=0.0, auto_blink=True, transition_ms=0,
                                )
                            else:
                                self._robot.expression_runtime.update(
                                    **_concurrency_expression(report["counts"][name] - 1)
                                )
                        elif name == "speaker":
                            playback = self._robot.audio.play_file(self._sample_audio)
                            if not self._wait_concurrency_audio(playback):
                                operation["interrupted"] = True
                                break
                        else:
                            photo = self._robot.camera.capture(timeout=10.0)
                            data = bytes(photo.data)
                            with Image.open(io.BytesIO(data)) as decoded:
                                if decoded.format != "JPEG":
                                    raise ValueError("Camera returned a non-JPEG image")
                                decoded.load()
                                width, height = decoded.size
                            filename = f"concurrency-{run_id}-{report['counts'][name]:03d}.jpg"
                            (self._artifacts_dir / filename).write_bytes(data)
                            operation["image"] = {"file": filename, "bytes": len(data), "width": width, "height": height}
                        operation["success"] = True
                        with report_lock:
                            report["counts"][name] += 1
                    except Exception as exc:
                        operation["error"] = str(exc) or type(exc).__name__
                        error(name, exc)
                        break
                    finally:
                        first_attempt = False
                        operation["end_s"] = time.monotonic() - started
                        with report_lock:
                            report["operations"].append(operation)
                        publish()
                    if name == "speaker":
                        break  # The legacy workload plays the sample exactly once.
                    # Delay after completion, not after the request's start time.
                    interval = 1.0 if name == "camera" else 0.0 if stage == "start" else 0.18
                    if time.monotonic() + interval >= deadline:
                        break
                    self._concurrency_cancel.wait(interval)

            def worker(name: str) -> None:
                try:
                    run_worker(name)
                finally:
                    # This channel is quiescent; another channel's capture must
                    # not delay stopping playback or restoring the display.
                    if self._concurrency_cancel.is_set() and name in ("speaker", "ui"):
                        cleanup_once(name)

            try:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    try:
                        futures = [pool.submit(worker, name) for name in ("ui", "speaker", "camera")]
                        with report_lock:
                            report["started"] = True
                        startup_complete.set()
                    finally:
                        # Release queued workers before the executor's __exit__
                        # joins them, even when submit raises after enqueueing.
                        gate.set()
                    while not all(future.done() for future in futures):
                        with report_lock:
                            report["samples"].append({"elapsed_s": time.monotonic() - started, "resources": dict(self._robot.resource_snapshot)})
                        publish()
                        time.sleep(0.5)
                    for future in futures:
                        future.result()
            except Exception as exc:
                error("setup", exc)
            finally:
                report["cancelled"] = self._concurrency_cancel.is_set() and not any(
                    not item["worker"].endswith("_cleanup") for item in report["errors"]
                )
                self._concurrency_cancel.set()
                for name in ("speaker", "ui"):
                    cleanup_once(name)
                report["running"] = False
                report["elapsed_s"] = time.monotonic() - started
                report["after"] = dict(self._robot.resource_snapshot)
                report["results"] = _concurrency_results(report)
                report["passed"] = all(result["status"] == "passed" for result in report["results"].values())
                report["incomplete"] = any(result["status"] == "incomplete" for result in report["results"].values())
                try:
                    saved_report = {**report, "report_saved": True}
                    (self._artifacts_dir / report["report"]).write_text(
                        json.dumps(saved_report, indent=2, ensure_ascii=False), encoding="utf-8",
                    )
                    report["report_saved"] = True
                except OSError as exc:
                    # Storage failures do not invalidate device evidence or
                    # prevent publishing the terminal state and cleanup result.
                    report["report_save_error"] = str(exc) or type(exc).__name__
                finally:
                    self._concurrency_report = report
            return report

    def stop_audio(self) -> dict[str, object]:
        self._ensure_device_online()
        self._robot.audio.stop()
        self._append_event("stop_audio", "Audio stop requested", "ok")
        return {"stopped": True}

    def move_motion(self, *, pan_deg: int, tilt_deg: int, duration_ms: int) -> dict[str, object]:
        with self._operation("motion_move", resource="motion"):
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
        with self._operation("light_color", resource="light"):
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
        with self._operation("light_effect", resource="light"):
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

    def play_animation(self, *, animation_id: str) -> dict[str, object]:
        self._validate_animation_id(animation_id)
        with self._operation("animation_play", resource="animation"):
            self._ensure_capability("animation")
            job = self._robot.animation.play(animation_id)
            return {
                "started": True,
                "operation_id": job.id,
                "animation_id": animation_id,
            }

    def prefetch_animation(self, *, animation_id: str) -> dict[str, object]:
        self._validate_animation_id(animation_id)
        with self._operation("animation_prefetch", resource="animation"):
            self._ensure_capability("animation.prefetch.v1")
            self._robot.animation.prefetch(animation_id)
            return {"prefetched": True, "animation_id": animation_id}

    def stop_animation(self) -> dict[str, object]:
        with self._procedural_lock, self._sd_baseline_lock:
            if self._procedural_lease is not None:
                raise MediaLabBusyError("Stop procedural animation with its own control first")
            if self._sd_baseline_lease is not None:
                raise MediaLabBusyError("Stop SD baseline with its own control first")
            self._ensure_device_online()
            self._ensure_capability("animation")
            self._robot.animation.stop()
        self._append_event("animation_stop", "Animation stop requested", "ok")
        return {"stopped": True}

    @staticmethod
    def _validate_animation_id(animation_id: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", animation_id) is None:
            raise ValueError("animation_id must be a catalog-safe resource id")

    def capture_photo(self) -> dict[str, object]:
        # The audio-follow firmware keeps its procedural display running during
        # plain still capture. Older SD feedback captures retain display exclusion.
        with self._procedural_lock:
            if self._procedural_lease is not None and self._procedural_state != "running":
                raise MediaLabBusyError("Confirm procedural lifecycle before capturing")
            resources = ("camera",) if self._procedural_state == "running" else ("camera", "animation")
            # Acquire before dropping the lifecycle lock so an idle capture's
            # display lease cannot race procedural startup. Do not keep that
            # lock during camera IO: status and resource recording must continue.
            lease = self._operation("capture_photo", resources=resources)
            lease.__enter__()
            preserves_procedural = self._procedural_state == "running"
            if preserves_procedural:
                self._procedural_captures += 1
        try:
            result = self._capture_photo()
        except Exception as error:
            lease.__exit__(type(error), error, error.__traceback__)
            raise
        else:
            lease.__exit__(None, None, None)
            return result
        finally:
            if preserves_procedural:
                with self._procedural_lock:
                    self._procedural_captures -= 1

    def capture_photo_with_feedback(self) -> dict[str, object]:
        self._ensure_capability("camera.capture.feedback.v1")
        with self._operation("capture_photo_with_feedback", resources=("camera", "animation", "microphone", "speaker")):
            return self._capture_photo(method=self._robot.camera.capture_with_feedback, artifact_name="camera-feedback.jpg")

    def _capture_photo(
        self, *, method: Callable[..., object] | None = None, artifact_name: str = "camera.jpg",
    ) -> dict[str, object]:
        image = (method or self._robot.camera.capture)(
            width=0,
            height=0,
            quality=0,
            timeout=10.0,
        )
        output = self._artifact_output(artifact_name)
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
        with self._operation("record_microphone", resources=("microphone", "speaker")):
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

    def start_live_video(self, *, mode: str = "video", request_id: str | None = None) -> dict[str, object]:
        if mode not in {"video", "audio", "av"}:
            raise ValueError("RTC mode must be video, audio, or av")
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
        action = {
            "audio": "rtc_audio",
            "video": "live_video",
            "av": "rtc_av",
        }[mode]
        rtc_resources = {
            "audio": ("microphone", "speaker"),
            "video": ("camera",),
            "av": ("camera", "microphone", "speaker"),
        }[mode]
        with self._live_video_lifecycle_lock:
            self._acquire_rtc_resources(action, rtc_resources)
            self._rtc_request_id = request_id
            try:
                self._ensure_device_online()
                self._browser_host_ipv4 = self._resolve_browser_host_ipv4()
                session = dict(self._rtc.start(mode=mode))
            except application_rtc.RtcSessionRejectedError as error:
                self._release_live_video_lock()
                if error.error == "busy":
                    owner = error.owner or "unknown"
                    raise MediaLabRtcError(
                        "rtc_resource_busy",
                        f"RTC media resource is busy: {owner}",
                        owner=error.owner,
                    ) from error
                raise MediaLabRtcError(
                    "rtc_start_rejected",
                    f"RTC session start rejected: {error.error}",
                    owner=error.owner,
                ) from error
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

    def stop_live_video(self, *, request_id: str | None = None) -> dict[str, object]:
        with self._live_video_lifecycle_lock:
            # Browser cleanup belongs to one start attempt, even if its ACK
            # arrives late. Unscoped controls retain the diagnostic/HIL API.
            if request_id is not None and request_id != self._rtc_request_id:
                return {"stopped": False, "matched": False}
            with self._state_lock:
                action = (
                    self._active_action
                    if self._active_action in {"live_video", "rtc_audio", "rtc_av"}
                    else "live_video"
                )
            stopped = bool(self._rtc.stop())
            self._release_live_video_lock()
        self._append_event(action, f"{_action_label(action)} session stopped", "ok")
        return {"stopped": stopped}

    def rtc_events(self, *, after: int = 0) -> list[dict[str, object]]:
        return self._rtc.events(after=max(0, after))

    def artifact_path(self, filename: str) -> Path | None:
        if filename not in self._ARTIFACT_TYPES and not _DIAGNOSTIC_ARTIFACT.fullmatch(filename) and not re.fullmatch(
            r"concurrency-[0-9a-f]{12}(?:-[0-9]{3,}\.jpg|\.json)", filename
        ):
            return None
        candidate = (self._artifacts_dir / filename).resolve()
        if candidate.parent != self._artifacts_dir.resolve():
            return None
        return candidate if candidate.is_file() else None

    def save_rtc_audio_diagnostic(self, channel: str, recording_id: str, data: bytes) -> dict[str, object]:
        """Publish immutable files and a manifest only for a complete local batch."""
        if not re.fullmatch(_DIAGNOSTIC_ID, recording_id):
            raise HTTPException(status_code=400, detail="Invalid diagnostic recording ID")
        channels = ("computer", "robot-raw", "robot-clean", "report")
        if channel not in channels:
            raise HTTPException(status_code=404, detail="Unknown diagnostic channel")
        if len(data) > (65536 if channel == "report" else 2 * 1024 * 1024):
            raise HTTPException(status_code=413, detail="Diagnostic upload too large")
        if channel == "report":
            try:
                report = json.loads(data)
                if not isinstance(report, dict) or not isinstance(report.get("samples"), list) or report.get("recordingId") != recording_id:
                    raise ValueError("Invalid report")
            except (ValueError, UnicodeDecodeError) as error:
                raise HTTPException(status_code=400, detail="Invalid diagnostic report") from error
        elif not data.startswith(b"\x1a\x45\xdf\xa3"):
            # A bounded local diagnostic checks the EBML signature, not full
            # container or codec validity. It never decodes caller uploads.
            raise HTTPException(status_code=400, detail="Diagnostic audio must have a WebM signature")
        files = {name: f"rtc-diagnostic-{recording_id}-{name}.{'json' if name == 'report' else 'webm'}" for name in channels}
        with self._diagnostic_lock:
            self._artifacts_dir.mkdir(parents=True, exist_ok=True)
            path = self._artifacts_dir / files[channel]
            if path.exists():
                raise HTTPException(status_code=409, detail="Diagnostic channel is already saved")
            if channel == "report" and any(not (self._artifacts_dir / files[name]).is_file() for name in channels[:-1]):
                raise HTTPException(status_code=409, detail="Diagnostic audio batch is incomplete")
            self._write_diagnostic_atomic(path, data)
            urls = {name: f"/artifacts/{filename}" for name, filename in files.items()}
            result: dict[str, object] = {"saved": True, "complete": channel == "report", "recording_id": recording_id,
                                         "bytes": len(data), "url": urls[channel]}
            if channel == "report":
                manifest = {"recording_id": recording_id, "files": urls}
                manifest_data = json.dumps(manifest).encode("utf-8")
                self._write_diagnostic_atomic(self._artifacts_dir / f"rtc-diagnostic-{recording_id}-manifest.json", manifest_data)
                self._write_diagnostic_atomic(self._artifacts_dir / "rtc-diagnostic-latest.json", manifest_data)
                result["files"] = urls
            return result

    @staticmethod
    def _write_diagnostic_atomic(path: Path, data: bytes) -> None:
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

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
            "state": str(status.get("state") or "unavailable") if type(status.get("online")) is bool else "unavailable",
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
            active = self._live_video_lock_held and any(
                self._active_actions.get(resource) in {"live_video", "rtc_audio", "rtc_av"}
                for resource in self._rtc_resources_held
            )
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
            self._rtc_request_id = None
            resources = self._rtc_resources_held
            self._rtc_resources_held = ()
            for resource in resources:
                if self._active_actions.get(resource) in {"live_video", "rtc_audio", "rtc_av"}:
                    self._active_actions.pop(resource, None)
            self._refresh_active_action_locked()
        for resource in reversed(resources):
            self._resource_locks[resource].release()

    def _acquire_rtc_resources(self, action: str, resources: tuple[str, ...]) -> None:
        acquired: list[str] = []
        for resource in sorted(resources):
            if self._resource_locks[resource].acquire(blocking=False):
                acquired.append(resource)
                continue
            for acquired_resource in reversed(acquired):
                self._resource_locks[acquired_resource].release()
            with self._state_lock:
                active_action = self._active_actions.get(resource) or self._active_action or "another action"
            raise MediaLabBusyError(f"media lab is busy with {active_action}")
        with self._state_lock:
            self._live_video_lock_held = True
            self._rtc_resources_held = tuple(sorted(resources))
            for resource in self._rtc_resources_held:
                self._active_actions[resource] = action
            self._refresh_active_action_locked()

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
    def _operation(
        self,
        action: str,
        *,
        resource: str | None = None,
        resources: tuple[str, ...] | None = None,
        retain_resources: set[str] | None = None,
    ) -> Iterator[None]:
        selected_resources = tuple(sorted(set(resources or ((resource or "speaker"),))))
        acquired: list[str] = []
        for selected_resource in selected_resources:
            if self._resource_locks[selected_resource].acquire(blocking=False):
                acquired.append(selected_resource)
                continue
            for acquired_resource in reversed(acquired):
                self._resource_locks[acquired_resource].release()
            with self._state_lock:
                active_action = (
                    self._active_actions.get(selected_resource)
                    or self._active_action
                    or "another action"
                )
            raise MediaLabBusyError(f"media lab is busy with {active_action}")
        try:
            self._ensure_device_online()
        except Exception:
            for acquired_resource in reversed(acquired):
                self._resource_locks[acquired_resource].release()
            raise
        with self._state_lock:
            for selected_resource in selected_resources:
                self._active_actions[selected_resource] = action
            self._refresh_active_action_locked()
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
                for selected_resource in selected_resources:
                    if self._active_actions.get(selected_resource) == action:
                        if retain_resources and selected_resource in retain_resources:
                            self._active_actions[selected_resource] = "concurrency_cleanup"
                        else:
                            self._active_actions.pop(selected_resource, None)
                self._refresh_active_action_locked()
            for acquired_resource in reversed(acquired):
                if not retain_resources or acquired_resource not in retain_resources:
                    self._resource_locks[acquired_resource].release()

    def _refresh_active_action_locked(self) -> None:
        self._active_action = next(iter(self._active_actions.values()), None)

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
            service.request_shutdown()
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
            try:
                if service._rtc.snapshot().get("active") is True:
                    await asyncio.to_thread(service.stop_live_video)
            except Exception:
                _LOGGER.exception("RTC shutdown could not be confirmed")
            try:
                await asyncio.to_thread(service.stop_sd_baseline)
            except Exception:
                _LOGGER.exception("SD baseline shutdown could not be confirmed")
            try:
                await asyncio.to_thread(service.stop_inference)
            except Exception:
                _LOGGER.exception("Inference shutdown could not be confirmed")
            try:
                await asyncio.to_thread(service.stop_face_tracking)
            except Exception:
                _LOGGER.exception("Face tracking shutdown could not be confirmed")
            try:
                await asyncio.to_thread(service.stop_procedural)
            except Exception:
                _LOGGER.exception("Procedural shutdown could not be confirmed")
            await asyncio.to_thread(service.stop_scenario_recording)

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
            "script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; connect-src 'self' ws:; frame-ancestors 'none'"
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
        content = {"error": error.code, "message": str(error)}
        if error.owner:
            content["owner"] = error.owner
        return JSONResponse(
            status_code=409,
            content=content,
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

    @app.post("/api/diagnostics/rtc-audio/{channel}")
    async def save_rtc_audio_diagnostic(channel: str, request: Request) -> dict[str, object]:
        if channel not in {"computer", "robot-raw", "robot-clean", "report"}:
            raise HTTPException(status_code=404, detail="Unknown diagnostic channel")
        limit = 65536 if channel == "report" else 2 * 1024 * 1024
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > limit:
                raise HTTPException(status_code=413, detail="Diagnostic upload too large")
            data.extend(chunk)
        return await _run_action(service.save_rtc_audio_diagnostic, channel=channel,
                                 recording_id=request.headers.get("X-Recording-Id", ""), data=bytes(data))

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

    @app.get("/api/diagnostics/rtc-speech")
    async def diagnostic_speech() -> FileResponse:
        return FileResponse(service.diagnostic_speech_path(), media_type="audio/wav")

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

    @app.post("/api/concurrency/start")
    async def run_concurrency_test(request: ConcurrencyTestRequest) -> dict[str, Any]:
        return await _run_action(
            service.run_concurrency_test,
            duration=request.duration,
        )

    @app.post("/api/concurrency/stop")
    async def stop_concurrency_test() -> dict[str, object]:
        return service.stop_concurrency_test()

    @app.post("/api/concurrency/cleanup")
    async def retry_concurrency_cleanup() -> dict[str, object]:
        return await _run_action(service.retry_concurrency_cleanup)

    @app.get("/api/concurrency/result")
    async def concurrency_result() -> dict[str, Any]:
        return service._concurrency_report or {}

    @app.post("/api/actions/play-audio")
    async def play_audio() -> dict[str, object]:
        return await _run_action(service.play_audio)

    @app.post("/api/actions/stop-audio")
    async def stop_audio() -> dict[str, object]:
        return await _run_action(service.stop_audio)

    @app.get("/api/vision/models")
    async def vision_models() -> dict[str, object]:
        return await _run_action(service.vision_models)

    @app.post("/api/vision/inference/start")
    async def start_inference(request: InferenceStartRequest) -> dict[str, object]:
        return await _run_action(lambda: service.start_inference(request.model_id, preview=request.preview))

    @app.post("/api/vision/inference/stop")
    async def stop_inference() -> dict[str, object]:
        return await _run_action(service.stop_inference)

    @app.get("/api/vision/inference/result")
    async def inference_result() -> dict[str, object]:
        return await _run_action(service.inference_result)

    @app.get("/api/vision/status")
    async def vision_status() -> dict[str, object]:
        return await _run_action(service.vision_status)

    @app.post("/api/face-tracking/start")
    async def start_face_tracking() -> dict[str, object]:
        return await _run_action(service.start_face_tracking)

    @app.post("/api/face-tracking/preview/start")
    async def start_face_preview() -> dict[str, object]:
        return await _run_action(lambda: service.start_face_tracking(preview=True))

    @app.get("/api/face-tracking/preview/frame")
    async def face_preview_frame() -> dict[str, object]:
        return await _run_action(service.face_preview_frame)

    @app.post("/api/face-tracking/stop")
    async def stop_face_tracking() -> dict[str, object]:
        return await _run_action(service.stop_face_tracking)

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

    @app.post("/api/controls/animation/play")
    async def play_animation(request: AnimationPlayRequest) -> dict[str, object]:
        return await _run_action(service.play_animation, animation_id=request.animation_id)

    @app.post("/api/controls/animation/prefetch")
    async def prefetch_animation(request: AnimationPlayRequest) -> dict[str, object]:
        return await _run_action(service.prefetch_animation, animation_id=request.animation_id)

    @app.post("/api/controls/animation/stop")
    async def stop_animation() -> dict[str, object]:
        return await _run_action(service.stop_animation)

    @app.post("/api/controls/procedural/start")
    async def start_procedural() -> dict[str, object]:
        return await _run_action(service.start_procedural)

    @app.post("/api/controls/procedural/stop")
    async def stop_procedural() -> dict[str, object]:
        return await _run_action(service.stop_procedural)

    @app.post("/api/controls/sd-baseline/start")
    async def start_sd_baseline() -> dict[str, object]:
        return await _run_action(service.start_sd_baseline)

    @app.post("/api/controls/sd-baseline/stop")
    async def stop_sd_baseline() -> dict[str, object]:
        return await _run_action(service.stop_sd_baseline)

    @app.post("/api/scenario/recording/start")
    async def start_scenario_recording(request: ScenarioRecordingStartRequest) -> dict[str, object]:
        return await _run_action(service.start_scenario_recording, label=request.label)

    @app.post("/api/scenario/recording/stop")
    async def stop_scenario_recording() -> dict[str, object]:
        return await _run_action(service.stop_scenario_recording)

    @app.get("/api/scenario/report")
    async def scenario_report() -> JSONResponse:
        report = await asyncio.to_thread(service.scenario_report)
        return JSONResponse(report, headers={"Content-Disposition": 'attachment; filename="media-lab-scene.json"'})

    @app.post("/api/actions/capture-photo")
    async def capture_photo() -> dict[str, object]:
        result = await _run_action(service.capture_photo)
        result["artifact_url"] = _artifact_url(str(result["artifact"]))
        return result

    @app.post("/api/actions/capture-photo-with-feedback")
    async def capture_photo_with_feedback() -> dict[str, object]:
        result = await _run_action(service.capture_photo_with_feedback)
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
        return await _run_action(service.start_live_video, mode=request.mode, request_id=request.request_id)

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
    async def stop_video(request: RtcSessionStopRequest | None = None) -> dict[str, object]:
        return await _run_action(service.stop_live_video, request_id=request.request_id if request else None)

    @app.get("/artifacts/{filename}")
    async def artifact(filename: str) -> FileResponse:
        path = service.artifact_path(filename)
        if path is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(path, media_type=MediaLabService._ARTIFACT_TYPES.get(
            filename, "application/json" if filename.endswith(".json") else "audio/webm" if filename.endswith(".webm") else "image/jpeg"
        ))

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
