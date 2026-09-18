from __future__ import annotations

import threading
import time
from collections import deque


class ResourceQueueClosedError(RuntimeError):
    """Raised when a queued resource request is interrupted by shutdown."""

    def __init__(self, reason: str = "closed") -> None:
        super().__init__(f"resource queue is closed: {reason}")
        self.reason = reason


class ResourceTicket:
    def __init__(self, scheduler: FifoResourceScheduler, sequence: int) -> None:
        self._scheduler = scheduler
        self.sequence = sequence
        self._state = "queued"
        self._reason: str | None = None
        self._closed = False

    @property
    def state(self) -> str:
        with self._scheduler._condition:
            return self._state

    @property
    def granted(self) -> bool:
        return self.state == "granted"

    def wait(self, timeout: float | None = None) -> ResourceTicket:
        deadline = None if timeout is None else time.monotonic() + max(timeout, 0.0)
        with self._scheduler._condition:
            while self._state == "queued":
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    self._scheduler._cancel_locked(self, "timeout")
                    raise TimeoutError("resource request did not start before timeout")
                self._scheduler._condition.wait(remaining)
            if self._state == "granted":
                return self
            if self._closed:
                raise ResourceQueueClosedError(self._reason or "closed")
            raise RuntimeError(f"resource request was cancelled: {self._reason or 'cancelled'}")

    def cancel(self, reason: str = "cancelled") -> bool:
        with self._scheduler._condition:
            return self._scheduler._cancel_locked(self, reason)

    def release(self) -> None:
        with self._scheduler._condition:
            self._scheduler._release_locked(self)


class FifoResourceScheduler:
    """A small FIFO gate for one device resource group."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._queue: deque[ResourceTicket] = deque()
        self._owner: ResourceTicket | None = None
        self._closed = False
        self._closed_reason = "closed"
        self._next_sequence = 1

    def request(self) -> ResourceTicket:
        with self._condition:
            if self._closed:
                raise ResourceQueueClosedError(self._closed_reason)
            ticket = ResourceTicket(self, self._next_sequence)
            self._next_sequence += 1
            self._queue.append(ticket)
            self._grant_next_locked()
            return ticket

    def close(self, reason: str = "closed") -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._closed_reason = reason
            for ticket in tuple(self._queue):
                ticket._closed = True
                self._cancel_locked(ticket, reason)
            self._condition.notify_all()

    def _grant_next_locked(self) -> None:
        if self._closed or self._owner is not None:
            return
        while self._queue:
            ticket = self._queue.popleft()
            if ticket._state != "queued":
                continue
            ticket._state = "granted"
            self._owner = ticket
            self._condition.notify_all()
            return

    def _cancel_locked(self, ticket: ResourceTicket, reason: str) -> bool:
        if ticket._state != "queued":
            return False
        ticket._state = "cancelled"
        ticket._reason = reason
        try:
            self._queue.remove(ticket)
        except ValueError:
            pass
        self._condition.notify_all()
        self._grant_next_locked()
        return True

    def _release_locked(self, ticket: ResourceTicket) -> None:
        if ticket._state == "queued":
            self._cancel_locked(ticket, "released")
            return
        if self._owner is not ticket:
            return
        ticket._state = "released"
        self._owner = None
        self._condition.notify_all()
        self._grant_next_locked()
