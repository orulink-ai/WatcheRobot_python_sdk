"""Daemon-owned structured runtime log buffer and persistence."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict


class DaemonLogEvent(TypedDict):
    id: int
    message: str
    timestamp_ms: int


class DaemonLogService:
    """Keep current-session Daemon logs queryable regardless of process owner."""

    def __init__(
        self,
        *,
        log_path: Path,
        max_entries: int = 500,
        clock_ms: Callable[[], int] | None = None,
        initial_id: int | None = None,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._log_path = Path(log_path)
        self._max_entries = max_entries
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._next_id = (
            int(initial_id)
            if initial_id is not None
            else self._clock_ms() * 1_000
        )
        self._entries: list[DaemonLogEvent] = []
        self._lock = threading.Lock()
        self.last_write_error: str | None = None

    def record(self, message: str) -> DaemonLogEvent:
        text = str(message).strip()
        if not text:
            raise ValueError("message must not be empty")
        with self._lock:
            self._next_id += 1
            event: DaemonLogEvent = {
                "id": self._next_id,
                "message": text,
                "timestamp_ms": self._clock_ms(),
            }
            try:
                self._append_record(event)
            except OSError as exc:
                self.last_write_error = str(exc)
            self._entries.append(event)
            if len(self._entries) > self._max_entries:
                del self._entries[: len(self._entries) - self._max_entries]
            return _copy_event(event)

    def recent(self, *, after_id: int = 0) -> list[DaemonLogEvent]:
        with self._lock:
            return [
                _copy_event(event)
                for event in self._entries
                if event["id"] > after_id
            ]

    def _append_record(self, event: DaemonLogEvent) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(
                json.dumps(
                    event,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            log_file.write("\n")


def _copy_event(event: DaemonLogEvent) -> DaemonLogEvent:
    return {
        "id": event["id"],
        "message": event["message"],
        "timestamp_ms": event["timestamp_ms"],
    }
