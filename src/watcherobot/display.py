"""Bounded JPEG display streaming over the managed Application Device channel.

These APIs block: asynchronous Applications should call them via ``to_thread``.
"""
from __future__ import annotations

import math
import threading
import time
from concurrent.futures import Future
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .errors import CommandError, WatcheRobotError
from .protocol import FLAG_FIRST, FLAG_LAST, FRAME_DISPLAY, build_wspk

if TYPE_CHECKING:
    from .robot import WatcheRobot


def _deadline(timeout: float) -> float:
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not 0 < timeout <= 120 or not math.isfinite(timeout)):
        raise ValueError("timeout must be finite and between 0 and 120 seconds")
    return time.monotonic() + timeout


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("display operation timed out")
    return remaining


def _ack_data(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict) or not isinstance(response.get("data"), dict):
        raise WatcheRobotError("display open ACK requires a data object")
    data: dict[str, Any] = response["data"]
    stream_id = data.get("stream_id")
    limit = data.get("max_frame_bytes")
    lease = data.get("lease_ms")
    if type(stream_id) is not int or not 1 <= stream_id <= 65535:
        raise WatcheRobotError("display open ACK requires stream_id in 1..65535")
    if type(limit) is not int or not 1 <= limit <= 65536:
        raise WatcheRobotError("display open ACK requires max_frame_bytes in 1..65536")
    if "lease_ms" in data and (type(lease) is not int or lease <= 0):
        raise WatcheRobotError("display open ACK lease_ms must be a positive integer")
    return data


class DisplayDomain:
    """Own at most one display stream, including opens and cleanup in progress."""

    def __init__(self, robot: WatcheRobot) -> None:
        self._robot = robot
        # One condition protects ownership and session state, including inline
        # transport callbacks. Never hold it across a synchronous command.
        self._condition = threading.Condition()
        self._session: DisplaySession | None = None
        self._opening = False
        self._closed = False
        self._generation = 0

    def open_stream(
        self, *, width: int = 412, height: int = 412, timeout: float = 3.0,
    ) -> DisplaySession:
        """Reserve the 412 x 412 logical canvas for bounded JPEG presentation.

        JPEG pixel dimensions (206 or 412 on supported firmware) are independent
        of the logical canvas. Firmware performs decoding and presentation.
        """
        deadline = _deadline(timeout)
        if type(width) is not int or type(height) is not int or (width, height) != (412, 412):
            raise ValueError("display canvas width and height must both be 412")
        self._robot._require_capability("display.stream.v1")
        with self._condition:
            if self._closed:
                raise WatcheRobotError("display domain is closed")
            if self._opening or self._session is not None:
                raise WatcheRobotError("display session already open, opening, or closing")
            self._opening = True
            generation = self._generation
        try:
            response = self._robot._command(
                "ctrl.display.open", {"width": width, "height": height, "format": "jpeg"},
                timeout=_remaining(deadline),
            )
            data = _ack_data(response)
            with self._condition:
                if generation != self._generation:
                    raise WatcheRobotError("display device disconnected during open")
                session = DisplaySession(self, data["stream_id"], data["max_frame_bytes"], data.get("lease_ms"))
                self._session = session
                if self._closed:
                    # Shutdown waits for the reservation, then closes this ID.
                    session._unusable = True
                    raise WatcheRobotError("display domain closed during open")
                try:
                    _remaining(deadline)
                except TimeoutError:
                    # A late ACK retains ownership for public close_stream cleanup.
                    session._unusable = True
                    raise
                return session
        finally:
            with self._condition:
                self._opening = False
                self._condition.notify_all()

    def _on_event(self, data: Any) -> None:
        """Receive the data object of ``evt.display.presented`` from Robot."""
        if not isinstance(data, dict):
            return
        stream_id, sequence = data.get("stream_id"), data.get("sequence")
        device_sequence, code = data.get("device_sequence"), data.get("code")
        if (type(stream_id) is not int or type(sequence) is not int
                or type(device_sequence) is not int or not 0 <= device_sequence <= 0xFFFFFFFF
                or type(code) is not int):
            return
        with self._condition:
            session = self._session
            if (session is None or session.stream_id != stream_id
                    or session._inflight != sequence or session._closed
                    or session._closing or session._unusable or session._presented is not None):
                return
            # Store only the current response, never idle, stale, or future ACKs.
            reason = data.get("reason")
            session._presented = (code, reason if isinstance(reason, str) else "display_failed")
            self._condition.notify_all()

    def _disconnected(self) -> None:
        """Retire local ownership and wake waiters without sending a command."""
        with self._condition:
            self._generation += 1
            if self._session is not None:
                self._session._closed = True
                self._session._unusable = True
                self._session._reason = "display device disconnected"
                self._session._presented = None
                self._session = None
            self._condition.notify_all()

    def close_stream(self, *, timeout: float = 3.0) -> None:
        """Release the owned stream, including one acquired by a late open ACK.

        This does not close the display domain: a new stream may be opened after
        successful cleanup. If cleanup fails, ownership is retained and this
        method may be retried with a new finite timeout. With no confirmed
        ownership, no device command is sent.
        """
        deadline = _deadline(timeout)
        with self._condition:
            while self._opening:
                self._condition.wait(_remaining(deadline))
            session = self._session
        if session is not None:
            session.close(timeout=_remaining(deadline))

    def _close(self, *, timeout: float = 3.0) -> None:
        """Close the owned stream before Robot closes its transport."""
        deadline = _deadline(timeout)
        with self._condition:
            self._closed = True
            while self._opening:
                self._condition.wait(_remaining(deadline))
            session = self._session
        if session is not None:
            session.close(timeout=_remaining(deadline))


class DisplaySession:
    """A synchronous display lease with one frame in flight and one ACK slot.

    A failed or timed-out ``present`` makes the handle unusable. Explicit
    ``close`` remains available, and failed cleanup can be retried.
    """

    def __init__(
        self, domain: DisplayDomain, stream_id: int, max_frame_bytes: int, lease_ms: int | None,
    ) -> None:
        self._domain = domain
        self._stream_id = stream_id
        self._max_frame_bytes = max_frame_bytes
        self._lease_ms = lease_ms
        self._next_sequence = 1
        self._inflight: int | None = None
        self._presented: tuple[int, str] | None = None
        self._unusable = False
        self._closed = False
        self._closing = False
        self._reason = ""

    @property
    def stream_id(self) -> int:
        return self._stream_id

    @property
    def max_frame_bytes(self) -> int:
        return self._max_frame_bytes

    @property
    def lease_ms(self) -> int | None:
        return self._lease_ms

    def _check_usable(self) -> None:
        if (self._domain._session is not self or self._closed or self._closing or self._unusable):
            raise WatcheRobotError(self._reason or "display session is closed or unusable; close it explicitly")

    def _send_done(self, future: Future[None]) -> None:
        with self._domain._condition:
            self._domain._condition.notify_all()

    def present(self, jpeg: bytes, timeout: float = 3.0) -> int:
        """Send a complete JPEG, wait for presentation, and return its SDK sequence.

        The same finite deadline covers the transport send and device ACK. JPEG
        markers and negotiated byte size are checked before sending anything.
        """
        deadline = _deadline(timeout)
        if not isinstance(jpeg, bytes):
            raise TypeError("jpeg must be bytes")
        if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise ValueError("jpeg requires SOI and EOI markers")
        if len(jpeg) > self.max_frame_bytes:
            raise ValueError("jpeg exceeds negotiated max_frame_bytes")
        condition = self._domain._condition
        with condition:
            self._check_usable()
            if self._inflight is not None:
                raise WatcheRobotError("display presentation already in flight")
            if self._next_sequence > 0xFFFFFFFF:
                self._unusable = True
                raise WatcheRobotError("display sequence exhausted; close the session")
            sequence = self._next_sequence
            self._next_sequence += 1
            self._inflight = sequence
            self._presented = None
        future: Future[None] | None = None
        try:
            packet = build_wspk(FRAME_DISPLAY, FLAG_FIRST | FLAG_LAST, self.stream_id, sequence, jpeg)
            with condition:
                self._check_usable()
                _remaining(deadline)
                future = self._domain._robot._transport.send_device(packet)
                future.add_done_callback(self._send_done)
                # Wait interruptibly even if a running Future cannot be cancelled.
                while not future.done():
                    self._check_usable()
                    condition.wait(_remaining(deadline))
                self._check_usable()
                future.result(timeout=_remaining(deadline))
                while self._presented is None:
                    self._check_usable()
                    condition.wait(_remaining(deadline))
                self._check_usable()
                _remaining(deadline)
                code, reason = self._presented
                if code != 0:
                    raise CommandError("evt.display.presented", reason)
                return sequence
        except BaseException:
            with condition:
                self._unusable = True
                self._presented = None
            if future is not None:
                future.cancel()
            raise
        finally:
            with condition:
                self._inflight = None
                self._presented = None
                condition.notify_all()

    def close(self, *, timeout: float = 3.0) -> None:
        """Release only this owned stream; retry after a failed close if needed."""
        deadline = _deadline(timeout)
        condition = self._domain._condition
        with condition:
            while self._closing and not self._closed:
                condition.wait(_remaining(deadline))
            if self._closed:
                return
            if self._domain._session is not self:
                self._closed = True
                return
            self._closing = True
            self._unusable = True
            self._presented = None
            condition.notify_all()
        def admit() -> None:
            # Runs on the transport loop immediately before command admission,
            # not just before scheduling its coroutine from this worker thread.
            with condition:
                if self._closed or self._domain._session is not self:
                    raise WatcheRobotError("display device disconnected before close admission")
                _remaining(deadline)

        try:
            self._domain._robot._command_guarded(
                "ctrl.display.close", {"stream_id": self.stream_id},
                timeout=_remaining(deadline), admit=admit,
            )
            with condition:
                self._closed = True
                if self._domain._session is self:
                    self._domain._session = None
        finally:
            with condition:
                self._closing = False
                condition.notify_all()

    def __enter__(self) -> DisplaySession:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
