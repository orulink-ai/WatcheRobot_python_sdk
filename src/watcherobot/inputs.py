from __future__ import annotations

import queue
import threading
from math import isfinite
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from .errors import WatcheRobotError


@dataclass(frozen=True, kw_only=True)
class InputDisplayContext:
    """Optional identity of the displayed frame on which an input occurred."""

    event_id: int | None = None
    stream_id: int | None = None
    display_sequence: int | None = None
    age_ms: int | None = None


@dataclass(frozen=True)
class BackTouchEvent(InputDisplayContext):
    """A press state reported by the rear touch sensor."""

    action: Literal["press", "release", "long_press"]
    touch_id: int
    timestamp_ms: int

    @property
    def source(self) -> Literal["back_touch"]:
        return "back_touch"


@dataclass(frozen=True)
class ScreenTouchEvent(InputDisplayContext):
    """A short tap reported by the display touch controller."""

    x: int
    y: int
    timestamp_ms: int

    @property
    def source(self) -> Literal["screen_touch"]:
        return "screen_touch"

    @property
    def action(self) -> Literal["tap"]:
        return "tap"


@dataclass(frozen=True)
class RollerEvent(InputDisplayContext):
    """A signed rotation step reported by the physical roller."""

    delta: int
    timestamp_ms: int

    @property
    def source(self) -> Literal["roller"]:
        return "roller"

    @property
    def action(self) -> Literal["rotate"]:
        return "rotate"


@dataclass(frozen=True)
class RollerButtonEvent(InputDisplayContext):
    """A debounced short press of the roller button."""

    timestamp_ms: int

    @property
    def source(self) -> Literal["roller"]:
        return "roller"

    @property
    def action(self) -> Literal["press"]:
        return "press"


InputEvent: TypeAlias = BackTouchEvent | ScreenTouchEvent | RollerEvent | RollerButtonEvent


def parse_input_event(data: object) -> InputEvent | None:
    if not isinstance(data, dict):
        return None
    timestamp_ms = data.get("timestamp_ms")
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms < 0:
        return None

    context: dict[str, int] = {}
    fields = {"id": "event_id", "stream_id": "stream_id",
              "display_sequence": "display_sequence", "age_ms": "age_ms"}
    if any(name in data for name in fields):
        for wire_name, attribute in fields.items():
            value = data.get(wire_name)
            minimum = 1 if wire_name in ("id", "stream_id") else 0
            maximum = 65535 if wire_name == "stream_id" else 0xFFFFFFFF
            if type(value) is not int or not minimum <= value <= maximum:
                return None
            context[attribute] = value

    source = data.get("source")
    action = data.get("action")
    if source == "back_touch":
        touch_id = data.get("touch_id")
        if (
            action not in ("press", "release", "long_press")
            or isinstance(touch_id, bool)
            or not isinstance(touch_id, int)
            or not 0 <= touch_id <= 255
        ):
            return None
        return BackTouchEvent(action=action, touch_id=touch_id, timestamp_ms=timestamp_ms, **context)

    if source == "screen_touch":
        x = data.get("x")
        y = data.get("y")
        if (
            action != "tap"
            or isinstance(x, bool)
            or not isinstance(x, int)
            or isinstance(y, bool)
            or not isinstance(y, int)
            or x < 0
            or y < 0
        ):
            return None
        return ScreenTouchEvent(x=x, y=y, timestamp_ms=timestamp_ms, **context)

    if source == "roller" and action == "press":
        return RollerButtonEvent(timestamp_ms=timestamp_ms, **context)

    if source == "roller":
        delta = data.get("delta")
        if (
            action != "rotate"
            or isinstance(delta, bool)
            or not isinstance(delta, int)
            or delta == 0
        ):
            return None
        return RollerEvent(delta=delta, timestamp_ms=timestamp_ms, **context)

    return None


_CLOSED = object()


class InputDomain:
    """Bounded synchronous input-event stream for the active SDK session."""

    def __init__(self, *, queue_size: int = 64) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._queue: queue.Queue[InputEvent | object] = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._dropped_events = 0
        self._closed_reason: str | None = None

    @property
    def dropped_events(self) -> int:
        with self._lock:
            return self._dropped_events

    def wait(self, timeout: float | None = None) -> InputEvent:
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be a finite non-negative number")
        with self._lock:
            closed_reason = self._closed_reason
        if closed_reason is not None:
            raise WatcheRobotError(f"input stream closed: {closed_reason}")
        try:
            event = self._queue.get(timeout=timeout) if timeout is not None else self._queue.get()
        except queue.Empty as error:
            raise TimeoutError("no input event received before timeout") from error
        if event is _CLOSED:
            with self._lock:
                reason = self._closed_reason or "connection_closed"
            raise WatcheRobotError(f"input stream closed: {reason}")
        return event  # type: ignore[return-value]

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _push(self, event: InputEvent) -> None:
        with self._lock:
            if self._closed_reason is not None:
                return
        while True:
            try:
                self._queue.put_nowait(event)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    continue
                with self._lock:
                    self._dropped_events += 1

    def _close(self, reason: str) -> None:
        with self._lock:
            if self._closed_reason is not None:
                return
            self._closed_reason = reason
        self.clear()
        self._queue.put_nowait(_CLOSED)
