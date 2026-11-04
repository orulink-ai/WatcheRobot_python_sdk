"""Async, content-agnostic serial connection implementing WRU1.

Constructing UsbConnection is inert. Explicitly await start() (open() is an
alias) before sending commands. on_frame(payload: str|bytes) and
on_disconnect(error: BaseException|None) are async callbacks on the owning
event loop. Frames are delivered in order; callbacks may await send()/close().
No JSON, business types, identity messages, scanning, or auto-open is involved.
management_identity is optional out-of-band metadata for the parent registry.

serial_factory is a blocking callable accepting pyserial-style keyword args:
port, baudrate, timeout, write_timeout; it returns an already-open serial port
with the pyserial in_waiting property.
The default lazily imports pyserial on start, constructs a closed port,
deasserts DTR/RTS, then assigns the port and opens it without flushing input.
Serial open/write/close run via
asyncio.to_thread; a dedicated worker reads available bytes (at most 1044),
waiting for only one byte while idle, with a bounded read timeout. It routes
ACKs and complete frames through a locked, bounded handoff, with at most one
queued loop notification. Up to 128 queued/active payloads share a 1 MiB Python
payload-storage budget (including Unicode storage); decoder state is separately
bounded. Overflow stops the reader immediately, then closes on the owning loop.
A single matching-ACK slot bypasses frame callbacks; stale ACKs are discarded.

send(str|bytes) serializes entire logical frames; each fragment waits for its
exact ACK within ack_timeout (including write time), followed by fragment_gap.
The gap uses worker-thread OS sleeps and a high-resolution deadline, preserving
the minimum gap without Windows asyncio timer latency or busy-spinning.
There are no retransmissions. I/O failure, ACK timeout, or cancellation during
transmission closes the session, physically closes the port, and wakes senders.
Invalid local payloads fail before writing and leave the connection usable.
close(code=1000, reason="...") is terminal and idempotent; construct a new
connection to reconnect. wait_closed() waits for cleanup and raises the original
failure, including callback errors; error also exposes the first failure.
Disconnect callback and cleanup errors are propagated by close()/wait_closed().
The factory must honor bounded read/write timeouts and close must unblock I/O.
See usb_wire's module contract for the firmware packet layout and decoder API.
"""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
import math
import sys
import threading
import time
from typing import Any, Protocol, cast

from .usb_wire import (
    HEADER_SIZE,
    MAX_FRAGMENT_PAYLOAD,
    UsbAck,
    UsbFrame,
    UsbWireDecoder,
    encode_frame,
)


class SerialPort(Protocol):
    @property
    def is_open(self) -> bool: ...
    @property
    def in_waiting(self) -> int: ...
    def read(self, size: int) -> bytes: ...
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...


class UsbConnectionClosed(ConnectionError):
    """The explicit serial session is not open or has closed."""


class UsbAckTimeout(TimeoutError):
    """A fragment write/ACK deadline elapsed; the session cannot be reused."""


def _serial_factory(**kwargs: Any) -> SerialPort:
    import serial

    port = kwargs.pop("port")
    serial_port = serial.Serial(port=None, **kwargs)
    try:
        # Opening with pyserial's asserted defaults can reset or halt CH342
        # boards. Set both states while closed, before assigning/opening port.
        serial_port.dtr = False
        serial_port.rts = False
        serial_port.port = port
        serial_port.open()
    except BaseException:
        serial_port.close()
        raise
    return cast(SerialPort, serial_port)


class UsbConnection:
    """WebSocket-shaped send/close interface with explicit asynchronous startup."""

    def __init__(
        self,
        port: str,
        baudrate: int = 1000000,
        *,
        on_frame: Callable[[str | bytes], Awaitable[None]],
        on_disconnect: Callable[[BaseException | None], Awaitable[None]],
        serial_factory: Callable[..., SerialPort] | None = None,
        ack_timeout: float = 1.0,
        fragment_gap: float = 0.001,
        read_timeout: float = 0.05,
        fragment_timeout: float = 1.0,
        max_noise: int = 4096,
        management_identity: Mapping[str, object] | None = None,
    ) -> None:
        if not port or type(baudrate) is not int or baudrate <= 0:
            raise ValueError("an explicit port and positive baudrate are required")
        for name, value in (("ack_timeout", ack_timeout), ("read_timeout", read_timeout)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not math.isfinite(fragment_gap) or fragment_gap < 0:
            raise ValueError("fragment_gap must be finite and nonnegative")
        self.port, self.baudrate = port, baudrate
        self.ack_timeout, self.fragment_gap = ack_timeout, fragment_gap
        self.read_timeout = min(read_timeout, fragment_timeout)
        self.management_identity = dict(management_identity or {})
        self.error: BaseException | None = None
        self.close_code: int | None = None
        self.close_reason = ""
        self._factory = serial_factory or _serial_factory
        self._on_frame, self._on_disconnect = on_frame, on_disconnect
        self._decoder = UsbWireDecoder(fragment_timeout=fragment_timeout, max_noise=max_noise)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._serial: SerialPort | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = asyncio.Event()
        self._closed_event = asyncio.Event()
        self._closed = self._started = False
        self._start_lock, self._tx_lock = asyncio.Lock(), asyncio.Lock()
        self._open_task: asyncio.Task[SerialPort] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._close_caller: asyncio.Task[Any] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._close_error: BaseException | None = None
        self._receive_lock = threading.Lock()
        self._frames: deque[tuple[str | bytes, int]] = deque()
        self._frames_ready = asyncio.Event()
        self._receive_count = self._receive_bytes = 0
        self._receive_notified = False
        self._receive_error: BaseException | None = None
        self._expected_ack: tuple[int, int] | None = None
        self._received_ack: UsbAck | None = None
        self._pending: tuple[int, int, asyncio.Future[None]] | None = None
        self._next_frame_id = 1

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def is_open(self) -> bool:
        return self._started and not self._closed

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("UsbConnection belongs to a different event loop")
        return loop

    def _require_open(self) -> None:
        if not self.is_open:
            raise UsbConnectionClosed(self.close_reason or "USB connection is not started")

    async def start(self) -> None:
        """Open only this explicit port, and await reader readiness."""
        self._bind_loop()
        async with self._start_lock:
            if self._closed:
                raise UsbConnectionClosed(self.close_reason or "USB connection is closed")
            if self._started:
                return
            self._open_task = asyncio.create_task(asyncio.to_thread(
                self._factory, port=self.port, baudrate=self.baudrate,
                timeout=self.read_timeout, write_timeout=self.ack_timeout,
            ))
            try:
                self._serial = await asyncio.shield(self._open_task)
                if self._closed:
                    raise UsbConnectionClosed(self.close_reason)
                self._dispatch_task = asyncio.create_task(self._dispatch_frames())
                self._reader = threading.Thread(
                    target=self._read_worker, args=(self._serial,),
                    name="watcherobot-usb-reader", daemon=True,
                )
                self._reader.start()
                await self._ready.wait()
                self._require_open()
            except BaseException as exc:
                await asyncio.shield(self._request_close(exc, 1011, "USB startup failed"))
                raise

    async def open(self) -> None:
        await self.start()

    def _reader_ready(self) -> None:
        if not self._closed:
            self._started = True
        self._ready.set()

    def _read_worker(self, serial_port: SerialPort) -> None:
        loop = self._loop
        assert loop is not None
        loop.call_soon_threadsafe(self._reader_ready)
        try:
            while not self._stop.is_set():
                read_size = max(
                    1, min(serial_port.in_waiting, HEADER_SIZE + MAX_FRAGMENT_PAYLOAD),
                )
                data = serial_port.read(read_size)
                if self._stop.is_set():
                    break
                if not data and not serial_port.is_open:
                    raise OSError("USB serial port disconnected")
                for event in self._decoder.feed(data):
                    if not self._receive_event(event):
                        return
        except Exception as exc:
            with self._receive_lock:
                if self._stop.is_set():
                    return
                self._receive_error = exc
                self._stop.set()
            self._notify_receive()

    def _reader_failed(self, error: BaseException) -> None:
        self._request_close(error, 1011, "USB reader failed")

    def _receive_event(self, event: UsbFrame | UsbAck) -> bool:
        """Reader-only admission; no event payload is captured by a loop handle."""
        with self._receive_lock:
            if self._stop.is_set():
                return False
            if isinstance(event, UsbAck):
                if self._expected_ack != (event.frame_id, event.fragment_index):
                    return True
                self._received_ack = event  # One matching ACK, never an ACK queue.
            else:
                # Wire-byte or character counts undercount wide Python strings.
                size = sys.getsizeof(event.payload)
                if self._receive_count >= 128 or self._receive_bytes + size > 1024 * 1024:
                    self._receive_error = BufferError("USB receive queue is full")
                    self._stop.set()
                else:
                    self._frames.append((event.payload, size))
                    self._receive_count += 1
                    self._receive_bytes += size
        self._notify_receive()
        return not self._stop.is_set()

    def _notify_receive(self) -> None:
        loop = self._loop
        assert loop is not None
        with self._receive_lock:
            if self._receive_notified:
                return
            self._receive_notified = True
        loop.call_soon_threadsafe(self._drain_receive)

    def _drain_receive(self) -> None:
        """Coalesced loop notification; ACKs do not wait for on_frame()."""
        with self._receive_lock:
            self._receive_notified = False
            error, ack = self._receive_error, self._received_ack
            self._received_ack = None
            have_frames = bool(self._frames)
        if self._closed:
            return
        if error is not None:
            self._reader_failed(error)
            return
        if ack is not None:
            pending = self._pending
            if pending is not None and pending[:2] == (ack.frame_id, ack.fragment_index):
                if not pending[2].done():
                    pending[2].set_result(None)
        if have_frames:
            self._frames_ready.set()

    async def _dispatch_frames(self) -> None:
        try:
            while not self._closed:
                await self._frames_ready.wait()
                with self._receive_lock:
                    if not self._frames:
                        self._frames_ready.clear()
                        continue
                    payload, size = self._frames.popleft()
                    if not self._frames:
                        self._frames_ready.clear()
                try:
                    await self._on_frame(payload)
                finally:
                    # Keep active callback payloads charged, including cancellation.
                    del payload
                    with self._receive_lock:
                        self._receive_count -= 1
                        self._receive_bytes -= size
        except asyncio.CancelledError as exc:
            if not self._closed:
                self._request_close(exc, 1011, "USB frame callback cancelled")
        except Exception as exc:
            self._request_close(exc, 1011, "USB frame callback failed")

    def _wait_fragment_gap(self, deadline: float) -> None:
        """Sleep off-loop until the real gap elapsed; close bounds long waits."""
        while not self._stop.is_set():
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return
            # time.sleep uses the OS sleep path rather than Windows asyncio's
            # coarse event-loop timer. Every iteration sleeps, without spinning.
            time.sleep(min(remaining, 0.05))


    def _write_packet(self, packet: bytes) -> None:
        serial_port = self._serial
        assert serial_port is not None
        offset = 0
        deadline = time.monotonic() + self.ack_timeout
        while offset < len(packet):
            if self._stop.is_set():
                raise UsbConnectionClosed("USB connection closed during write")
            if time.monotonic() >= deadline:
                raise UsbAckTimeout("USB fragment write deadline elapsed")
            written = serial_port.write(packet[offset:])
            if not isinstance(written, int) or not 0 < written <= len(packet) - offset:
                raise OSError("USB serial write made no progress")
            offset += written

    async def send(self, payload: str | bytes) -> None:
        """Send an opaque payload with stop-and-wait ACKs; no retransmission."""
        loop = self._bind_loop()
        self._require_open()
        async with self._tx_lock:
            self._require_open()
            frame_id = self._next_frame_id
            packets = encode_frame(payload, frame_id=frame_id)
            self._next_frame_id = frame_id % 0xFFFFFFFF + 1
            for index, packet in enumerate(packets):
                self._require_open()
                ack: asyncio.Future[None] = loop.create_future()
                self._pending = (frame_id, index, ack)
                with self._receive_lock:
                    self._expected_ack = (frame_id, index)
                    self._received_ack = None
                deadline = loop.time() + self.ack_timeout
                writing = asyncio.create_task(asyncio.to_thread(self._write_packet, packet))
                try:
                    # asyncio.wait_for on Python 3.10/3.11 can swallow external
                    # cancellation when the inner write completes concurrently.
                    # wait preserves cancellation and leaves explicit ownership
                    # of both the write task and the ACK future here.
                    done, _ = await asyncio.wait({writing}, timeout=max(0, deadline - loop.time()))
                    if not done:
                        raise UsbAckTimeout("USB fragment write deadline elapsed")
                    writing.result()
                    done, _ = await asyncio.wait({ack}, timeout=max(0, deadline - loop.time()))
                    if not done:
                        raise UsbAckTimeout(
                            f"USB ACK timeout for frame {frame_id} fragment {index}"
                        )
                    ack.result()
                    self._pending = None
                    with self._receive_lock:
                        self._expected_ack = None
                        self._received_ack = None
                    if self.fragment_gap:
                        gap_deadline = time.perf_counter() + self.fragment_gap
                        await asyncio.to_thread(self._wait_fragment_gap, gap_deadline)
                    self._require_open()
                except BaseException as exc:
                    if not ack.done():
                        ack.cancel()
                    closing = self._request_close(exc, 1011, "USB send failed")
                    # An external close cancels and gathers the dispatcher.
                    # Let it unwind instead of waiting for its own collector.
                    # A dispatcher-owned close already excludes its caller.
                    if (asyncio.current_task() is not self._dispatch_task
                            or self._close_caller is self._dispatch_task):
                        await asyncio.shield(closing)
                    raise
                finally:
                    if not writing.done():
                        writing.cancel()
                    await asyncio.gather(writing, return_exceptions=True)
                    if self._pending is not None and self._pending[2] is ack:
                        self._pending = None
                    with self._receive_lock:
                        self._expected_ack = None
                        self._received_ack = None
                    if ack.done() and not ack.cancelled():
                        ack.exception()  # Retrieve a close error even if the write failed first.

    def _request_close(
        self, error: BaseException | None, code: int, reason: str,
    ) -> asyncio.Task[None]:
        self._bind_loop()
        if self._close_task is None:
            self._closed = True
            self.error, self.close_code, self.close_reason = error, code, reason
            self._close_caller = asyncio.current_task()
            self._stop.set()
            with self._receive_lock:
                self._receive_count -= len(self._frames)
                self._receive_bytes -= sum(size for _, size in self._frames)
                self._frames.clear()
                self._expected_ack = None
                self._received_ack = None
                self._receive_error = None
            self._frames_ready.set()
            self._ready.set()
            if self._pending is not None and not self._pending[2].done():
                self._pending[2].set_exception(UsbConnectionClosed(reason))
            self._close_task = asyncio.create_task(self._finish_close())
        return self._close_task

    async def _finish_close(self) -> None:
        try:
            if self._open_task is not None:
                try:
                    self._serial = await asyncio.shield(self._open_task)
                except Exception:
                    pass  # start() reports factory failure; no handle was returned.
            if self._serial is not None:
                try:
                    await asyncio.to_thread(self._serial.close)
                except Exception as exc:
                    self._close_error = exc
            if self._reader is not None:
                await asyncio.to_thread(self._reader.join, self.read_timeout + 1.0)
                if self._reader.is_alive() and self._close_error is None:
                    self._close_error = RuntimeError("USB reader did not stop after serial close")
            dispatcher = self._dispatch_task
            if dispatcher is not None and dispatcher is not self._close_caller and not dispatcher.done():
                dispatcher.cancel()
                await asyncio.gather(dispatcher, return_exceptions=True)
            if self.error is None:
                self.error = self._close_error
            try:
                await self._on_disconnect(self.error)
            except (Exception, asyncio.CancelledError) as exc:
                self._close_error = exc
                if self.error is None:
                    self.error = exc
        finally:
            self._closed_event.set()

    async def close(self, code: int = 1000, reason: str = "USB connection closed") -> None:
        """Permanently close the session, release the port, and wake ACK waits."""
        task = self._request_close(None, code, reason)
        caller = asyncio.current_task()
        if caller is task:
            return  # Allow on_disconnect to call close() without self-awaiting.
        if caller is self._dispatch_task and self._close_caller is not caller:
            # The external close gathers this callback, including its finally.
            # Return so it can unwind; external callers still await all cleanup.
            return
        await asyncio.shield(task)
        if self._close_error is not None:
            raise self._close_error

    async def wait_closed(self) -> None:
        """Wait for port cleanup and surface reader, callback, or send failures."""
        self._bind_loop()
        await self._closed_event.wait()
        if self._close_error is not None:
            raise self._close_error
        if self.error is not None:
            raise self.error
