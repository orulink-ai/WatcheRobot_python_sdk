"""Application-owned control surface for Watcher peer-to-peer RTC sessions."""

from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from watcherobot.application.transport import DaemonApplicationTransport


RTC_PROTOCOL = "watcher-rtc/1"
RTC_AUDIO_CAPABILITY = "rtc.audio.full_duplex.v1"
RTC_VIDEO_CAPABILITY = "rtc.video.mjpeg.v1"
_MODES = {"video", "audio", "av"}


class ApplicationRtc:
    """Manage one browser-facing RTC session through the current Application."""

    def __init__(
        self,
        transport: DaemonApplicationTransport,
        *,
        id_factory: Callable[[], str] | None = None,
        send_timeout: float = 2.0,
    ) -> None:
        self._transport = transport
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._send_timeout = send_timeout
        self._lock = threading.RLock()
        self._client_id: str | None = None
        self._session_id: str | None = None
        self._mode: str | None = None
        self._state = "idle"
        self._last_error: str | None = None
        self._capabilities: dict[str, Any] = {}
        self._stats: dict[str, Any] = {}
        self._event_sequence = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=256)
        self._active = False
        self._stop_sent = False
        self._closed = False
        transport.add_message_listener(self._on_message)

    def start(self, *, mode: str = "video") -> dict[str, Any]:
        if mode not in _MODES:
            raise ValueError("mode must be one of: video, audio, av")
        with self._lock:
            self._ensure_open()
            if self._active:
                raise RuntimeError("an RTC session is already active")
            self._client_id = self._new_id("client")
            self._session_id = self._new_id("session")
            self._mode = mode
            self._state = "starting"
            self._last_error = None
            self._capabilities = {}
            self._stats = {}
            self._events.clear()
            self._event_sequence = 0
            self._active = True
            self._stop_sent = False
        try:
            self._send("ctrl.rtc.session.start", {"mode": mode})
        except Exception:
            with self._lock:
                self._active = False
                self._state = "failed"
            raise
        return self.snapshot()

    def send_offer(self, sdp: str) -> None:
        if not isinstance(sdp, str) or not sdp or len(sdp.encode("utf-8")) > 16384:
            raise ValueError("sdp must be a non-empty UTF-8 string up to 16384 bytes")
        self._send("ctrl.rtc.signal", {"kind": "offer", "sdp": sdp})

    def send_candidate(
        self,
        candidate: str,
        *,
        sdp_mid: str,
        sdp_mline_index: int,
    ) -> None:
        if (
            not isinstance(candidate, str)
            or not candidate
            or len(candidate.encode("utf-8")) > 2048
        ):
            raise ValueError("candidate must be a non-empty UTF-8 string up to 2048 bytes")
        if not isinstance(sdp_mid, str) or len(sdp_mid.encode("utf-8")) > 64:
            raise ValueError("sdp_mid must be a UTF-8 string up to 64 bytes")
        if (
            isinstance(sdp_mline_index, bool)
            or not isinstance(sdp_mline_index, int)
            or not 0 <= sdp_mline_index <= 65535
        ):
            raise ValueError("sdp_mline_index must be an integer between 0 and 65535")
        self._send(
            "ctrl.rtc.signal",
            {
                "kind": "candidate",
                "candidate": candidate,
                "sdp_mid": sdp_mid,
                "sdp_mline_index": sdp_mline_index,
            },
        )

    def clock_ping(self, browser_send_us: int) -> None:
        if (
            isinstance(browser_send_us, bool)
            or not isinstance(browser_send_us, int)
            or not 0 < browser_send_us <= 9_007_199_254_740_991
        ):
            raise ValueError("browser_send_us must be a positive safe integer")
        self._send("ctrl.rtc.clock.ping", {"browser_send_us": browser_send_us})

    def feedback(self, **metrics: int) -> None:
        required = {
            "display_fps_x100",
            "frame_age_p95_us",
            "rtt_us",
            "audio_queue_ms",
            "audio_packet_loss_x100",
            "audio_jitter_us",
            "audio_concealed_frames",
            "congestion_level",
        }
        if set(metrics) != required:
            raise ValueError("feedback metrics do not match the watcher-rtc/1 contract")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in metrics.values()):
            raise ValueError("feedback metrics must be non-negative integers")
        if metrics["congestion_level"] > 3:
            raise ValueError("congestion_level must be between 0 and 3")
        self._send("ctrl.rtc.feedback", dict(metrics))

    def stop(self) -> bool:
        with self._lock:
            if not self._active or self._stop_sent:
                return False
            previous_state = self._state
            self._stop_sent = True
            self._state = "stopping"
        try:
            self._send("ctrl.rtc.session.stop", {})
        except Exception:
            with self._lock:
                if self._active and self._stop_sent and self._state == "stopping":
                    self._stop_sent = False
                    self._state = previous_state
            raise
        with self._lock:
            self._active = False
        return True

    def reset(self, *, reason: str) -> bool:
        """Forget an active session that can no longer be signalled.

        This is intentionally local-only: callers use it after the Device channel is
        known to be offline, where sending ``ctrl.rtc.session.stop`` cannot succeed.
        Clearing the identifiers also prevents late events from the abandoned session
        from mutating a future session.
        """
        if not isinstance(reason, str) or not reason:
            raise ValueError("reason must be a non-empty string")
        with self._lock:
            if not self._active:
                return False
            self._active = False
            self._stop_sent = False
            self._state = "failed"
            self._last_error = reason
            self._client_id = None
            self._session_id = None
            self._mode = None
            self._capabilities = {}
            self._stats = {}
            self._events.clear()
            self._event_sequence = 0
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": self._active,
                "client_id": self._client_id,
                "session_id": self._session_id,
                "state": self._state,
                "mode": self._mode,
                "last_error": self._last_error,
                "capabilities": dict(self._capabilities),
                "stats": dict(self._stats),
            }

    def events(self, *, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events if event["id"] > after]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
        try:
            self.stop()
        except Exception:
            pass
        self._transport.remove_message_listener(self._on_message)
        with self._lock:
            self._closed = True

    def _send(self, message_type: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._ensure_open()
            if not self._active or self._client_id is None or self._session_id is None:
                raise RuntimeError("no active RTC session")
            envelope = {
                "type": message_type,
                "protocol": RTC_PROTOCOL,
                "client_id": self._client_id,
                "session_id": self._session_id,
                "command_id": self._new_id("command"),
                "data": data,
            }
        frame = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
        self._transport.send_device(frame).result(timeout=self._send_timeout)

    def _on_message(self, message: dict[str, Any]) -> None:
        with self._lock:
            if (
                message.get("protocol") != RTC_PROTOCOL
                or message.get("client_id") != self._client_id
                or message.get("session_id") != self._session_id
            ):
                return
            message_type = message.get("type")
            if not isinstance(message_type, str):
                return
            data = message.get("data")
            if not isinstance(data, dict):
                data = {}
            if message_type == "evt.rtc.state":
                state = data.get("state")
                if isinstance(state, str):
                    self._state = state
                    if state in {"stopped", "failed"}:
                        self._active = False
                reason = data.get("reason")
                if isinstance(reason, str) and reason:
                    self._last_error = reason
            elif message_type == "evt.rtc.capabilities":
                self._capabilities = dict(data)
            elif message_type == "evt.rtc.stats":
                self._stats = dict(data)
            elif message_type == "sys.nack":
                error = data.get("error") or data.get("reason") or "rtc_rejected"
                self._last_error = str(error)
                self._state = "failed"
                self._active = False
            self._event_sequence += 1
            self._events.append(
                {"id": self._event_sequence, "message": json.loads(json.dumps(message))}
            )

    def _new_id(self, kind: str) -> str:
        value = str(self._id_factory())
        if not 8 <= len(value) <= 63 or any(
            not (character.isascii() and (character.isalnum() or character in "._:-"))
            for character in value
        ):
            raise ValueError(f"{kind} id factory returned an invalid watcher-rtc/1 identifier")
        return value

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("RTC controller is closed")


__all__ = [
    "ApplicationRtc",
    "RTC_AUDIO_CAPABILITY",
    "RTC_PROTOCOL",
    "RTC_VIDEO_CAPABILITY",
]
