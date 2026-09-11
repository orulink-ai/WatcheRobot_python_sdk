"""Session-scoped, bounded latest-result access to on-device models."""
from __future__ import annotations

import secrets
import base64
import binascii
import threading
import time
from dataclasses import dataclass
from collections.abc import Iterator, Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .robot import WatcheRobot

from .errors import WatcheRobotError


def ack(response: object, message_type: str) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get("type") != "sys.ack" or response.get("code") != 0:
        raise WatcheRobotError("invalid vision ACK")
    data = response.get("data")
    if not isinstance(data, dict) or data.get("type") != message_type:
        raise WatcheRobotError("invalid vision ACK envelope")
    return data


def integer(data: Mapping[str, object], key: str, minimum: int = 0, maximum: int = 0xFFFFFFFF) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise WatcheRobotError(f"invalid vision {key}")
    return value


@dataclass(frozen=True)
class DetectionBox:
    """Detection with center-based coordinates, matching the device contract."""
    x: int
    y: int
    width: int
    height: int
    score: int
    target: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x, self.y


@dataclass(frozen=True)
class InferenceResult:
    session_id: int
    model_id: int
    sequence: int
    timestamp_ms: int
    frame_width: int
    frame_height: int
    task: str
    boxes: tuple[DetectionBox, ...]
    jpeg: bytes | None = None


class InferenceSession:
    """Headless inference; close only this session, never a newer owner.

    Results are device-side latest snapshots. Slow consumers skip older results.
    Optional JPEG output belongs to the same snapshot as the detection boxes.
    """

    def __init__(self, robot: WatcheRobot, model_id: int, *, preview: bool = False) -> None:
        self._robot = robot
        self.id = secrets.randbelow(0x7FFFFFFF) + 1
        self.model_id = model_id
        self.preview = preview
        self._closed = False
        self._lock = threading.RLock()

    @property
    def closed(self) -> bool:
        return self._closed

    def latest(self, *, timeout: float | None = None) -> InferenceResult | None:
        from .vision import _validate_timeout
        _validate_timeout(timeout)
        with self._lock:
            if self.closed:
                raise RuntimeError("inference session is closed")
            message_type = "ctrl.vision.inference.result.get"
            data = ack(self._robot._command(message_type, {"session_id": self.id}, timeout=timeout), message_type)
            if integer(data, "session_id", 1) != self.id:
                raise WatcheRobotError("vision result belongs to another session")
            if data.get("ready") is False:
                return None
            if data.get("ready") is not True or data.get("task") != "detection":
                raise WatcheRobotError("unsupported vision result task or readiness")
            model_id = integer(data, "model_id", 1, 255)
            if model_id != self.model_id:
                raise WatcheRobotError("vision result belongs to another model")
            width = integer(data, "frame_width", 1, 4096)
            height = integer(data, "frame_height", 1, 4096)
            raw = data.get("boxes")
            if not isinstance(raw, list) or len(raw) > 8:
                raise WatcheRobotError("invalid vision boxes")
            boxes = []
            for item in raw:
                if not isinstance(item, dict):
                    raise WatcheRobotError("invalid vision box")
                boxes.append(DetectionBox(integer(item, "x", 0, width), integer(item, "y", 0, height),
                                          integer(item, "width", 1, width), integer(item, "height", 1, height),
                                          integer(item, "score", 0, 100), integer(item, "target", 0, 255)))
            jpeg = None
            if self.preview:
                encoded = data.get("jpeg_base64")
                if not isinstance(encoded, str) or not 0 < len(encoded) <= 175000:
                    raise WatcheRobotError("invalid vision preview image")
                try:
                    jpeg = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise WatcheRobotError("invalid vision preview encoding") from exc
                if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
                    raise WatcheRobotError("invalid vision preview JPEG")
            return InferenceResult(self.id, model_id, integer(data, "sequence", 1),
                                   integer(data, "timestamp_ms"), width, height, "detection", tuple(boxes), jpeg)

    def results(self, *, poll_interval: float = 0.1) -> Iterator[InferenceResult]:
        """Yield new results until closed, with no accumulating background queue."""
        from .vision import _validate_timeout
        _validate_timeout(poll_interval)
        if poll_interval is None:
            raise ValueError("poll_interval must be positive")
        previous = None
        while not self.closed:
            result = self.latest()
            if result is not None and result.sequence != previous:
                previous = result.sequence
                yield result
            time.sleep(poll_interval)

    def close(self, *, timeout: float | None = 5.0) -> None:
        from .vision import _validate_timeout
        _validate_timeout(timeout)
        deadline = None if timeout is None else time.monotonic() + timeout
        acquired = self._lock.acquire() if timeout is None else self._lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError("inference result request did not become idle")
        try:
            if self.closed:
                return
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("inference stop budget exhausted")
            message_type = "ctrl.vision.inference.stop"
            data = ack(self._robot._command(message_type, {"session_id": self.id}, timeout=remaining), message_type)
            if integer(data, "session_id", 1) != self.id:
                raise WatcheRobotError("vision stop ACK has another session")
            self._closed = True
        finally:
            self._lock.release()

    def __enter__(self) -> InferenceSession:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()
