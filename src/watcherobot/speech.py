"""Device-owned JoyInside recognition and synthesis over the Application channel."""
from __future__ import annotations

import threading
import time
import uuid
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .errors import CommandError, WatcheRobotError

if TYPE_CHECKING:
    from .robot import WatcheRobot


def _deadline(timeout: float) -> float:
    if not 0 < timeout <= 120:
        raise ValueError("timeout must be between 0 and 120 seconds")
    return time.monotonic() + timeout


class SpeechDomain:
    def __init__(self, robot: WatcheRobot) -> None:
        self._robot = robot
        self._lock = threading.Lock()
        self._session: SpeechSession | None = None

    def open(self, *, timeout: float = 30) -> SpeechSession:
        """Reserve robot audio and reuse its stored JoyInside configuration.

        Recognition mode is ``conversation_asr``: JoyInside may still invoke
        its cloud agent. Its automatic audio is discarded by the firmware.
        """
        deadline = _deadline(timeout)
        self._robot._require_capability("speech.joyinside.v1")
        with self._lock:
            if self._session is not None:
                raise WatcheRobotError("speech session already open or closing")
            session = SpeechSession(self, uuid.uuid4().hex)
            self._session = session
        try:
            self._robot._command("ctrl.speech.open", {"session_id": session.id}, timeout=timeout)
            session._wait(deadline, ready=True)
            return session
        except CommandError:
            session._event({"kind": "closed", "reason": "open_rejected"})
            raise
        except BaseException:
            try:
                session.close()
            except Exception:
                pass
            raise

    def _event(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        with self._lock:
            session = self._session
        if session is not None and data.get("session_id") == session.id:
            session._event(data)

    def _release(self, session: SpeechSession) -> None:
        with self._lock:
            if self._session is session:
                self._session = None

    def _disconnected(self) -> None:
        with self._lock:
            session = self._session
        if session:
            session._event({"kind": "closed", "reason": "device_disconnected"})

    def _close(self) -> None:
        with self._lock:
            session = self._session
        if session:
            session.close()

    def close(self) -> None:
        """Retry cleanup, including a session whose open timed out."""
        self._close()


class SpeechSession:
    def __init__(self, domain: SpeechDomain, session_id: str) -> None:
        self._domain = domain
        self.id = session_id
        self.recognition_mode = ""
        self._condition = threading.Condition()
        self._ready = False
        self._closed = False
        self._closing = False
        self._reason = ""
        self._request = ""
        self._kind = ""
        self._result: str | None = None
        self._error = ""

    def _event(self, data: dict[str, Any]) -> None:
        with self._condition:
            kind = data.get("kind")
            if kind == "closed":
                self._closed = True
                self._reason = str(data.get("reason", "closed"))
                self._domain._release(self)
            elif self._closed:
                return
            elif kind == "ready" and data.get("recognition_mode") == "conversation_asr":
                self.recognition_mode = "conversation_asr"
                self._ready = True
            elif kind == "error" and not data.get("request_id"):
                self._error = str(data.get("reason", "speech_failed"))
            elif self._request and data.get("request_id") == self._request:
                if kind == "error":
                    self._error = str(data.get("reason", "speech_failed"))
                elif kind == "recognition" and self._kind == "recognize":
                    text = data.get("text")
                    if isinstance(text, str) and text.strip():
                        self._result = text
                elif kind == "playback" and self._kind == "say" and data.get("state") == "completed":
                    self._result = ""
            self._condition.notify_all()

    def _wait(self, deadline: float, *, ready: bool = False, closed: bool = False) -> str:
        with self._condition:
            while True:
                if closed and self._closed:
                    return ""
                if self._closed:
                    raise WatcheRobotError(self._reason)
                if self._error:
                    raise WatcheRobotError(self._error)
                if ready and self._ready:
                    return ""
                if not ready and not closed and self._result is not None:
                    return self._result
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("device speech operation timed out")
                self._condition.wait(remaining)

    def _run(self, kind: str, data: dict[str, Any], timeout: float) -> str:
        deadline = _deadline(timeout)
        with self._condition:
            if self._closed or self._closing:
                raise WatcheRobotError(self._reason or "speech session closing")
            if self._request:
                raise WatcheRobotError("speech operation already running")
            self._request = request_id = uuid.uuid4().hex
            self._kind, self._result, self._error = kind, None, ""
        try:
            self._domain._robot._command(
                f"ctrl.speech.{kind}",
                {"session_id": self.id, "request_id": request_id,
                 "timeout_ms": int(timeout * 1000), **data}, timeout=timeout,
            )
            return self._wait(deadline)
        except BaseException:
            with self._condition:
                connected = not self._closed
            if connected:
                try:
                    self._domain._robot._command(
                        "ctrl.speech.cancel", {"session_id": self.id, "request_id": request_id}, timeout=2,
                    )
                except Exception:
                    pass
            raise
        finally:
            with self._condition:
                self._request = ""

    def recognize(self, *, timeout: float = 30) -> str:
        """Capture the robot microphone until JoyInside returns final ASR."""
        return self._run("recognize", {}, timeout)

    def say(self, text: str, *, timeout: float = 60) -> None:
        """Synthesize specified text and wait until physical playback drains."""
        if not isinstance(text, str) or not text.strip() or "\0" in text or len(text.encode("utf-8")) >= 1000:
            raise ValueError("text must contain 1–999 UTF-8 bytes and non-whitespace text")
        self._run("say", {"text": text}, timeout)

    def cancel(self) -> None:
        with self._condition:
            request = self._request
            if self._closed or not request:
                return
        self._domain._robot._command("ctrl.speech.cancel", {"session_id": self.id, "request_id": request}, timeout=2)

    def close(self, *, timeout: float = 10) -> None:
        deadline = _deadline(timeout)
        with self._condition:
            if self._closed:
                return
            self._closing = True
            self._error = ""
        try:
            self._domain._robot._command("ctrl.speech.close", {"session_id": self.id}, timeout=timeout)
        except CommandError:
            with self._condition:
                if not self._closed:
                    raise
        self._wait(deadline, closed=True)

    def __enter__(self) -> SpeechSession:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()
