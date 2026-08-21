"""Typed device-vision and face-tracking APIs for managed Applications."""

from __future__ import annotations

import asyncio
import queue
import struct
import time
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from threading import Lock
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Literal, Mapping, TypeVar

from .errors import WatcheRobotError

if TYPE_CHECKING:
    from .robot import WatcheRobot


_IMAGE_HEADER = struct.Struct("<4sBBHIIHHI")
_CLOSED = object()
_SUPPORTED_RESOLUTIONS = frozenset({(240, 240), (416, 416), (640, 480)})
FaceTrackingStopPolicy = Literal["hold", "recenter"]
VisionHealth = Literal["idle", "ready", "busy", "degraded", "error"]
_ValueT = TypeVar("_ValueT")


@dataclass(frozen=True)
class VisionCapabilities:
    """Visual features exposed by the active device backend."""

    capture: bool
    preview: bool
    inference: bool
    model_info: bool
    model_management: bool


@dataclass(frozen=True)
class VisionModel:
    """Descriptor for the model currently active on the vision coprocessor."""

    model_id: int
    name: str
    task: str
    contains_face_class: bool


@dataclass(frozen=True)
class VisionStatus:
    """Point-in-time health and capability snapshot for the vision backend."""

    backend: str
    health: VisionHealth
    status_code: int
    initialized: bool
    connected: bool
    streaming: bool
    inferencing: bool
    capabilities: VisionCapabilities
    model: VisionModel | None


class VisionDomain:
    """Inspect model-independent device vision health and capabilities."""

    def __init__(self, robot: WatcheRobot) -> None:
        self._robot = robot

    def status(self, *, timeout: float | None = None) -> VisionStatus:
        _validate_timeout(timeout)
        self._robot._require_capability("vision.status.v1")
        response = self._robot._command("ctrl.vision.status.get", {}, timeout=timeout)
        return parse_vision_status_response(response)

    def health(self, *, timeout: float | None = None) -> VisionStatus:
        return self.status(timeout=timeout)

    def active_model(self, *, timeout: float | None = None) -> VisionModel | None:
        return self.status(timeout=timeout).model

    def capabilities(self, *, timeout: float | None = None) -> VisionCapabilities:
        return self.status(timeout=timeout).capabilities


@dataclass(frozen=True)
class FaceBox:
    """One face box in sensor pixel coordinates."""

    x: int
    y: int
    width: int
    height: int
    score: int
    target: int

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


@dataclass(frozen=True)
class FaceTrackingTelemetry:
    """Stable tracking fields paired with one preview image."""

    sequence: int
    timestamp_ms: int
    age_ms: int
    frame_width: int
    frame_height: int
    faces: tuple[FaceBox, ...]
    target_visible: bool
    error_x_percent: float
    error_y_percent: float
    pan_velocity_deg_s: float
    tilt_velocity_deg_s: float
    state: int
    command: int
    preprocess_ms: float | None = None
    inference_ms: float | None = None
    postprocess_ms: float | None = None
    raw: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class FaceTrackingFrame:
    """A complete JPEG and telemetry pair from the same Himax frame."""

    jpeg: bytes
    sequence: int
    device_timestamp_ms: int
    received_at: float
    width: int
    height: int
    telemetry: FaceTrackingTelemetry

    @property
    def faces(self) -> tuple[FaceBox, ...]:
        return self.telemetry.faces

    @property
    def content_type(self) -> Literal["image/jpeg"]:
        return "image/jpeg"

    def save(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(self.jpeg)
        return output


@dataclass(frozen=True)
class _PreviewImage:
    jpeg: bytes
    sequence: int
    timestamp_ms: int
    width: int
    height: int


class FaceTrackingPreview:
    """Bounded latest-frame stream returned by ``open_preview``.

    The session is already active when returned. Use it as a synchronous or
    asynchronous context manager so the device always receives a matching
    stop command.
    """

    def __init__(
        self,
        robot: WatcheRobot,
        *,
        stop_policy: FaceTrackingStopPolicy,
        queue_size: int,
    ) -> None:
        self._robot = robot
        self._stop_policy = stop_policy
        self._queue: queue.Queue[FaceTrackingFrame | object] = queue.Queue(
            maxsize=queue_size
        )
        self._lock = Lock()
        self._closed_reason: str | None = None
        self._dropped_frames = 0
        self._telemetry_by_sequence: dict[int, FaceTrackingTelemetry] = {}
        self._image_by_sequence: dict[int, _PreviewImage] = {}

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed_reason is not None

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    def read(self, timeout: float | None = None) -> FaceTrackingFrame:
        _validate_timeout(timeout)
        with self._lock:
            closed_reason = self._closed_reason
        if closed_reason is not None:
            raise WatcheRobotError(
                f"face tracking preview closed: {closed_reason}"
            )
        try:
            frame = (
                self._queue.get(timeout=timeout)
                if timeout is not None
                else self._queue.get()
            )
        except queue.Empty as error:
            raise TimeoutError("no face tracking preview frame before timeout") from error
        if frame is _CLOSED:
            with self._lock:
                reason = self._closed_reason or "connection_closed"
            raise WatcheRobotError(f"face tracking preview closed: {reason}")
        return frame  # type: ignore[return-value]

    async def read_async(
        self, timeout: float | None = None
    ) -> FaceTrackingFrame:
        return await asyncio.to_thread(self.read, timeout)

    def close(self) -> None:
        self._robot._close_face_tracking_preview(self, self._stop_policy)

    def __enter__(self) -> FaceTrackingPreview:
        if self.closed:
            raise WatcheRobotError("face tracking preview is already closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    async def __aenter__(self) -> FaceTrackingPreview:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await asyncio.to_thread(self.close)

    def __aiter__(self) -> FaceTrackingPreview:
        return self

    async def __anext__(self) -> FaceTrackingFrame:
        try:
            return await self.read_async()
        except WatcheRobotError:
            with self._lock:
                reason = self._closed_reason
            if reason == "closed":
                raise StopAsyncIteration from None
            raise

    def _push_telemetry(self, data: object) -> None:
        telemetry = parse_face_tracking_telemetry(data)
        if telemetry is None:
            return
        frame: FaceTrackingFrame | None = None
        with self._lock:
            if self._closed_reason is not None:
                return
            image = self._image_by_sequence.pop(telemetry.sequence, None)
            if image is None:
                self._telemetry_by_sequence[telemetry.sequence] = telemetry
                _trim_oldest(self._telemetry_by_sequence)
                return
            frame = _pair_frame(image, telemetry)
        if frame is not None:
            self._publish(frame)

    def _push_image(self, packet: bytes) -> None:
        image = parse_face_tracking_image(packet)
        if image is None:
            return
        frame: FaceTrackingFrame | None = None
        with self._lock:
            if self._closed_reason is not None:
                return
            telemetry = self._telemetry_by_sequence.pop(image.sequence, None)
            if telemetry is None:
                self._image_by_sequence[image.sequence] = image
                _trim_oldest(self._image_by_sequence)
                return
            frame = _pair_frame(image, telemetry)
        if frame is not None:
            self._publish(frame)

    def _publish(self, frame: FaceTrackingFrame) -> None:
        with self._lock:
            if self._closed_reason is not None:
                return
            while True:
                try:
                    self._queue.put_nowait(frame)
                    return
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        continue
                    self._dropped_frames += 1

    def _mark_closed(self, reason: str) -> None:
        with self._lock:
            if self._closed_reason is not None:
                return
            self._closed_reason = reason
            self._telemetry_by_sequence.clear()
            self._image_by_sequence.clear()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            self._queue.put_nowait(_CLOSED)


class FaceTrackingDomain:
    """Start a typed face-following preview session."""

    def __init__(self, robot: WatcheRobot) -> None:
        self._robot = robot

    def open_preview(
        self,
        *,
        width: int = 416,
        height: int = 416,
        frame_stride: int = 1,
        stop_policy: FaceTrackingStopPolicy = "hold",
        queue_size: int = 1,
    ) -> FaceTrackingPreview:
        _validate_preview_options(
            width=width,
            height=height,
            frame_stride=frame_stride,
            stop_policy=stop_policy,
            queue_size=queue_size,
        )
        return self._robot._open_face_tracking_preview(
            width=width,
            height=height,
            frame_stride=frame_stride,
            stop_policy=stop_policy,
            queue_size=queue_size,
        )

    def stop(self, *, policy: FaceTrackingStopPolicy = "hold") -> None:
        if policy not in ("hold", "recenter"):
            raise ValueError("policy must be hold or recenter")
        self._robot._stop_face_tracking_preview(policy)


def parse_face_tracking_image(packet: bytes) -> _PreviewImage | None:
    if len(packet) < _IMAGE_HEADER.size:
        return None
    magic, version, kind, header_size, sequence, timestamp_ms, width, height, jpeg_size = (
        _IMAGE_HEADER.unpack_from(packet)
    )
    if (
        magic != b"FTW1"
        or version != 1
        or kind != 1
        or header_size != _IMAGE_HEADER.size
        or width <= 0
        or height <= 0
        or jpeg_size < 4
        or header_size + jpeg_size != len(packet)
    ):
        return None
    jpeg = bytes(packet[header_size:])
    if not (jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")):
        return None
    return _PreviewImage(
        jpeg=jpeg,
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        width=width,
        height=height,
    )


def parse_vision_status_response(response: object) -> VisionStatus:
    if not isinstance(response, dict) or response.get("type") != "sys.ack":
        raise WatcheRobotError("vision status ACK is missing or invalid")
    data = response.get("data")
    if not isinstance(data, dict) or data.get("type") != "ctrl.vision.status.get":
        raise WatcheRobotError("vision status ACK has an invalid data envelope")
    backend = data.get("backend")
    health = data.get("health")
    status_code = data.get("status_code")
    if not isinstance(backend, str) or not backend:
        raise WatcheRobotError("vision status ACK has an invalid backend")
    if health not in {"idle", "ready", "busy", "degraded", "error"}:
        raise WatcheRobotError("vision status ACK has an invalid health state")
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise WatcheRobotError("vision status ACK has an invalid status code")

    initialized = _required_bool(data, "initialized")
    connected = _required_bool(data, "connected")
    streaming = _required_bool(data, "streaming")
    inferencing = _required_bool(data, "inferencing")
    raw_capabilities = data.get("capabilities")
    if not isinstance(raw_capabilities, dict):
        raise WatcheRobotError("vision status ACK has invalid capabilities")
    capabilities = VisionCapabilities(
        capture=_required_bool(raw_capabilities, "capture"),
        preview=_required_bool(raw_capabilities, "preview"),
        inference=_required_bool(raw_capabilities, "inference"),
        model_info=_required_bool(raw_capabilities, "model_info"),
        model_management=_required_bool(raw_capabilities, "model_management"),
    )

    raw_model = data.get("model")
    if not isinstance(raw_model, dict):
        raise WatcheRobotError("vision status ACK has an invalid model descriptor")
    model_available = _required_bool(raw_model, "available")
    model: VisionModel | None = None
    if model_available:
        model_id = _integer(raw_model.get("model_id"), minimum=0)
        model_name = raw_model.get("model_name")
        task = raw_model.get("task")
        contains_face_class = raw_model.get("contains_face_class")
        if (
            model_id is None
            or not isinstance(model_name, str)
            or not model_name
            or not isinstance(task, str)
            or not task
            or not isinstance(contains_face_class, bool)
        ):
            raise WatcheRobotError("vision status ACK has an invalid active model")
        model = VisionModel(
            model_id=model_id,
            name=model_name,
            task=task,
            contains_face_class=contains_face_class,
        )
    return VisionStatus(
        backend=backend,
        health=health,
        status_code=status_code,
        initialized=initialized,
        connected=connected,
        streaming=streaming,
        inferencing=inferencing,
        capabilities=capabilities,
        model=model,
    )


def parse_face_tracking_telemetry(data: object) -> FaceTrackingTelemetry | None:
    if not isinstance(data, dict) or data.get("v") != 1 or data.get("kind") != "frame":
        return None
    sequence = _integer(data.get("seq"), minimum=0, maximum=0xFFFFFFFF)
    timestamp_ms = _integer(data.get("t"), minimum=0, maximum=0xFFFFFFFF)
    age_ms = _integer(data.get("age", 0), minimum=0, maximum=0xFFFFFFFF)
    size = _number_pair(data.get("size"))
    error = _number_pair(data.get("error"))
    velocity = _number_pair(data.get("velocity"))
    if (
        sequence is None
        or timestamp_ms is None
        or age_ms is None
        or size is None
        or error is None
        or velocity is None
    ):
        return None
    width = _whole_number(size[0])
    height = _whole_number(size[1])
    if width is None or height is None or width <= 0 or height <= 0:
        return None

    faces: list[FaceBox] = []
    raw_boxes = data.get("boxes", [])
    if isinstance(raw_boxes, list):
        for raw_box in raw_boxes:
            parsed = _face_box(raw_box)
            if parsed is not None:
                faces.append(parsed)
    perf = _number_triplet(data.get("perf"))
    return FaceTrackingTelemetry(
        sequence=sequence,
        timestamp_ms=timestamp_ms,
        age_ms=age_ms,
        frame_width=width,
        frame_height=height,
        faces=tuple(faces),
        target_visible=bool(data.get("visible")),
        error_x_percent=error[0],
        error_y_percent=error[1],
        pan_velocity_deg_s=velocity[0],
        tilt_velocity_deg_s=velocity[1],
        state=_integer(data.get("state", 0), minimum=0) or 0,
        command=_integer(data.get("command", 0), minimum=0) or 0,
        preprocess_ms=perf[0] if perf is not None else None,
        inference_ms=perf[1] if perf is not None else None,
        postprocess_ms=perf[2] if perf is not None else None,
        raw=MappingProxyType(dict(data)),
    )


def _pair_frame(
    image: _PreviewImage,
    telemetry: FaceTrackingTelemetry,
) -> FaceTrackingFrame | None:
    if (
        image.sequence != telemetry.sequence
        or image.width != telemetry.frame_width
        or image.height != telemetry.frame_height
    ):
        return None
    return FaceTrackingFrame(
        jpeg=image.jpeg,
        sequence=image.sequence,
        device_timestamp_ms=image.timestamp_ms,
        received_at=time.time(),
        width=image.width,
        height=image.height,
        telemetry=telemetry,
    )


def _validate_preview_options(
    *,
    width: int,
    height: int,
    frame_stride: int,
    stop_policy: str,
    queue_size: int,
) -> None:
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
        or (width, height) not in _SUPPORTED_RESOLUTIONS
    ):
        raise ValueError("resolution must be 240x240, 416x416, or 640x480")
    if (
        isinstance(frame_stride, bool)
        or not isinstance(frame_stride, int)
        or not 1 <= frame_stride <= 3
    ):
        raise ValueError("frame_stride must be an integer between 1 and 3")
    if stop_policy not in ("hold", "recenter"):
        raise ValueError("stop_policy must be hold or recenter")
    if (
        isinstance(queue_size, bool)
        or not isinstance(queue_size, int)
        or not 1 <= queue_size <= 8
    ):
        raise ValueError("queue_size must be an integer between 1 and 8")


def _validate_timeout(timeout: float | None) -> None:
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not isfinite(timeout)
        or timeout < 0
    ):
        raise ValueError("timeout must be a finite non-negative number")


def _trim_oldest(target: dict[int, _ValueT], maximum: int = 8) -> None:
    while len(target) > maximum:
        target.pop(next(iter(target)))


def _integer(
    value: object,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _required_bool(data: Mapping[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise WatcheRobotError(f"vision status ACK has an invalid {key} flag")
    return value


def _whole_number(value: float) -> int | None:
    return int(value) if value.is_integer() else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if isfinite(converted) else None


def _number_pair(value: object) -> tuple[float, float] | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    first = _number(value[0])
    second = _number(value[1])
    return None if first is None or second is None else (first, second)


def _number_triplet(value: object) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) < 3:
        return None
    numbers = tuple(_number(item) for item in value[:3])
    if any(item is None for item in numbers):
        return None
    return numbers  # type: ignore[return-value]


def _face_box(value: object) -> FaceBox | None:
    if not isinstance(value, list) or len(value) < 6:
        return None
    fields: list[int] = []
    for item in value[:6]:
        parsed = _integer(item, minimum=0)
        if parsed is None:
            return None
        fields.append(parsed)
    x, y, width, height, score, target = fields
    if width <= 0 or height <= 0:
        return None
    return FaceBox(x, y, width, height, score, target)


__all__ = [
    "FaceBox",
    "FaceTrackingDomain",
    "FaceTrackingFrame",
    "FaceTrackingPreview",
    "FaceTrackingStopPolicy",
    "FaceTrackingTelemetry",
    "VisionCapabilities",
    "VisionDomain",
    "VisionHealth",
    "VisionModel",
    "VisionStatus",
    "parse_vision_status_response",
]
