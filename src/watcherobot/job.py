from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Protocol

from .errors import JobCancelledError, JobFailedError


class CommandTransport(Protocol):
    def send_command(self, message_type: str, data: dict, timeout: float | None = None) -> dict: ...


class JobState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (self.COMPLETED, self.FAILED, self.CANCELLED)


class Job:
    def __init__(
        self,
        operation_id: int,
        transport: CommandTransport,
        *,
        initial_state: JobState = JobState.STARTING,
    ) -> None:
        if operation_id <= 0:
            raise ValueError("operation_id must be positive")
        self._id = operation_id
        self._transport = transport
        self._state = initial_state
        self._error_code: int | None = None
        self._condition = threading.Condition()
        self._cancel_requested = False

    @property
    def id(self) -> int:
        return self._id

    @property
    def state(self) -> JobState:
        with self._condition:
            return self._state

    @property
    def error_code(self) -> int | None:
        with self._condition:
            return self._error_code

    def wait(self, timeout: float | None = None) -> Job:
        deadline = None if timeout is None else time.monotonic() + max(timeout, 0)
        with self._condition:
            while not self._state.terminal:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"Job {self._id} did not finish before timeout")
                self._condition.wait(remaining)
            if self._state is JobState.FAILED:
                detail = f" (error_code={self._error_code})" if self._error_code is not None else ""
                raise JobFailedError(f"Job {self._id} failed{detail}")
            if self._state is JobState.CANCELLED:
                raise JobCancelledError(f"Job {self._id} was cancelled")
            return self

    def cancel(self) -> None:
        with self._condition:
            if self._state.terminal or self._cancel_requested:
                return
            self._cancel_requested = True
        try:
            self._transport.send_command("ctrl.job.cancel", {"operation_id": self._id})
        except Exception:
            with self._condition:
                self._cancel_requested = False
            raise

    def _update(self, state: JobState | str, error_code: int | None = None) -> None:
        next_state = state if isinstance(state, JobState) else JobState(state)
        with self._condition:
            if self._state.terminal:
                return
            self._state = next_state
            self._error_code = error_code
            self._condition.notify_all()

