"""Reusable direct Desktop-to-Device session for exclusive CLI operations."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from concurrent.futures import Future
from typing import Any, Callable, Coroutine

from websockets.asyncio.client import connect

from ._internal.audio_status import AudioStatusKind, classify_audio_status
from .errors import CommandError, WatcheRobotError
from .protocol import (
    FLAG_FIRST,
    FLAG_LAST,
    FRAME_AUDIO,
    BinaryFrame,
    build_command,
    build_wspk,
    parse_json_message,
    parse_wspk,
)

AUDIO_DEVICE_SLOT_BYTES = 4096
AUDIO_MAX_CREDIT_PACKETS = 8


class DesktopRobotSession:
    """Synchronous transport over the Daemon Desktop socket.

    The Daemon remains an opaque router. Callers must ensure no Application is
    active before opening this session, because an active Application owns the
    Desktop and Device channels.
    """

    def __init__(self, url: str, *, command_timeout: float = 5.0) -> None:
        self.url = url
        self.command_timeout = command_timeout
        self.capabilities: tuple[str, ...] = ()
        self.animation_ids: tuple[str, ...] = ()
        self.device_info: dict[str, Any] = {}
        self.resource_baseline: dict[str, Any] = {}
        self.resource_rtc_baseline: dict[str, Any] = {}
        self.resource_snapshot: dict[str, Any] = {}
        self.resource_history: list[dict[str, Any]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._websocket: Any | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._started = threading.Event()
        self._startup_error: BaseException | None = None
        self._stop_event: asyncio.Event | None = None
        self._message_callback: Callable[[dict[str, Any]], None] = lambda _m: None
        self._binary_callback: Callable[[BinaryFrame], None] = lambda _f: None
        self._disconnect_callback: Callable[[], None] = lambda: None
        self._message_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._listeners_lock = threading.Lock()
        self._audio_condition: asyncio.Condition | None = None
        self._audio_stream_id = 0
        self._audio_credits = 0
        self._audio_slots_per_packet = 1
        self._audio_error: str | None = None

    def set_callbacks(
        self,
        message_callback: Callable[[dict[str, Any]], None],
        binary_callback: Callable[[BinaryFrame], None],
        disconnect_callback: Callable[[], None],
    ) -> None:
        self._message_callback = message_callback
        self._binary_callback = binary_callback
        self._disconnect_callback = disconnect_callback

    def add_message_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._listeners_lock:
            if callback not in self._message_listeners:
                self._message_listeners.append(callback)

    def remove_message_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._listeners_lock:
            if callback in self._message_listeners:
                self._message_listeners.remove(callback)

    def start(self, *, timeout: float = 5.0) -> None:
        if self._thread is not None:
            raise RuntimeError("Desktop session is already started")
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="watcherobot-cli-session")
        self._thread.start()
        if not self._started.wait(timeout):
            self.close()
            raise TimeoutError("Timed out connecting the CLI Desktop session")
        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            raise WatcheRobotError(f"CLI Desktop session failed: {error}") from error

    def close(self) -> None:
        if self._loop is not None and self._stop_event is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        self._thread = None

    def send_command(self, message_type: str, data: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        effective = self.command_timeout if timeout is None else timeout
        return self._submit(self._send_command(message_type, data, effective)).result(effective + 1.0)

    def send_command_nowait(self, message_type: str, data: dict[str, Any]) -> Future[dict[str, Any]]:
        return self._submit(self._send_command(message_type, data, self.command_timeout))

    def send_audio_stream(self, pcm: bytes, *, stream_id: int, chunk_bytes: int = 4096) -> Future[None]:
        if chunk_bytes != AUDIO_DEVICE_SLOT_BYTES:
            raise ValueError(f"chunk_bytes must be {AUDIO_DEVICE_SLOT_BYTES}")
        return self._submit(self._send_audio(bytes(pcm), stream_id, chunk_bytes))

    def send_device(self, frame: str | bytes) -> Future[None]:
        return self._submit(self._send(frame))

    def _submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        if self._loop is None or not self._loop.is_running():
            coroutine.close()
            raise WatcheRobotError("CLI Desktop session is not connected")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except BaseException as error:
            self._startup_error = error
            self._started.set()
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(WatcheRobotError("CLI Desktop session disconnected"))
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._loop = None

    async def _run(self) -> None:
        self._stop_event = asyncio.Event()
        async with connect(self.url, max_size=None) as websocket:
            self._websocket = websocket
            await websocket.send(json.dumps({"type": "sys.client.hello", "code": 0, "data": {"role": "desktop"}}, separators=(",", ":")))
            hello = await asyncio.wait_for(websocket.recv(), self.command_timeout)
            if not isinstance(hello, str) or parse_json_message(hello).get("type") != "sys.ack":
                raise WatcheRobotError("Daemon rejected the CLI Desktop session")
            reader = asyncio.create_task(self._reader())
            try:
                try:
                    await self._send_command("sys.sdk.ready.get", {}, min(1.0, self.command_timeout))
                except (CommandError, TimeoutError):
                    pass
                self._started.set()
                await self._stop_event.wait()
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
                self._websocket = None

    async def _reader(self) -> None:
        assert self._websocket is not None
        try:
            async for raw in self._websocket:
                if isinstance(raw, bytes):
                    try:
                        self._binary_callback(parse_wspk(raw))
                    except ValueError:
                        continue
                    continue
                message = parse_json_message(raw)
                data = message.get("data", {})
                with self._listeners_lock:
                    listeners = tuple(self._message_listeners)
                for listener in listeners:
                    try:
                        listener(message)
                    except Exception:
                        continue
                if message.get("type") == "evt.sdk.ready":
                    capabilities = data.get("capabilities", [])
                    if isinstance(capabilities, list):
                        self.capabilities = tuple(item for item in capabilities if isinstance(item, str))
                    animations = data.get("animations", [])
                    if isinstance(animations, list):
                        self.animation_ids = tuple(item for item in animations if isinstance(item, str))
                    self.device_info.update(data)
                if message.get("type") == "evt.audio.buffer_status":
                    await self._update_audio_flow(data)
                if message.get("type") in {"sys.ack", "sys.nack"}:
                    future = self._pending.get(data.get("command_id"))
                    if future is not None and not future.done():
                        future.set_result(message)
                elif message.get("type") == "sys.ping":
                    await self._send(json.dumps({"type": "sys.pong", "code": 0, "data": data}, separators=(",", ":")))
                else:
                    self._message_callback(message)
        finally:
            self._disconnect_callback()

    async def _send(self, frame: str | bytes) -> None:
        if self._websocket is None:
            raise WatcheRobotError("CLI Desktop session is not connected")
        await self._websocket.send(frame)

    async def _send_command(self, message_type: str, data: dict[str, Any], timeout: float) -> dict[str, Any]:
        command_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            await self._send(build_command(message_type, data, command_id))
            response = await asyncio.wait_for(future, timeout)
            if response.get("type") == "sys.nack":
                response_data = response.get("data", {})
                raise CommandError(message_type, str(response_data.get("reason") or response_data.get("error") or "unknown"))
            return response
        finally:
            self._pending.pop(command_id, None)

    async def _send_audio(self, pcm: bytes, stream_id: int, chunk_bytes: int) -> None:
        if self._audio_condition is None:
            self._audio_condition = asyncio.Condition()
        async with self._audio_condition:
            self._audio_stream_id = stream_id
            self._audio_slots_per_packet = max(1, (chunk_bytes + AUDIO_DEVICE_SLOT_BYTES - 1) // AUDIO_DEVICE_SLOT_BYTES)
            self._audio_credits = 4
            self._audio_error = None
        try:
            for sequence, offset in enumerate(range(0, len(pcm), chunk_bytes)):
                await self._take_audio_credit(stream_id)
                payload = pcm[offset:offset + chunk_bytes]
                flags = (FLAG_FIRST if offset == 0 else 0) | (FLAG_LAST if offset + len(payload) >= len(pcm) else 0)
                await self._send(build_wspk(FRAME_AUDIO, flags, stream_id, sequence, payload))
        finally:
            async with self._audio_condition:
                if self._audio_stream_id == stream_id:
                    self._audio_stream_id = 0
                    self._audio_credits = 0

    async def _take_audio_credit(self, stream_id: int) -> None:
        assert self._audio_condition is not None
        async def wait() -> None:
            assert self._audio_condition is not None
            async with self._audio_condition:
                await self._audio_condition.wait_for(lambda: self._audio_stream_id != stream_id or self._audio_credits > 0 or self._audio_error is not None)
                if self._audio_stream_id != stream_id:
                    raise WatcheRobotError("audio stream was replaced")
                if self._audio_error:
                    raise WatcheRobotError(f"audio stream failed: {self._audio_error}")
                self._audio_credits -= 1
        try:
            await asyncio.wait_for(wait(), self.command_timeout)
        except TimeoutError as error:
            raise WatcheRobotError("timed out waiting for robot audio buffer credit") from error

    async def _update_audio_flow(self, data: dict[str, Any]) -> None:
        if self._audio_condition is None or data.get("stream_id") != self._audio_stream_id:
            return
        async with self._audio_condition:
            reason = str(data.get("reason", ""))
            if classify_audio_status(reason) is AudioStatusKind.FAILED:
                self._audio_error = reason
            else:
                free = data.get("free_frames")
                pending = data.get("pending_frames")
                depth = data.get("queue_depth")
                if isinstance(pending, int) and isinstance(depth, int) and depth > 0:
                    slots = max(0, max(4, depth // 2) - pending)
                elif isinstance(free, int):
                    slots = max(0, free)
                else:
                    slots = 0
                self._audio_credits = min(AUDIO_MAX_CREDIT_PACKETS, slots // self._audio_slots_per_packet)
            self._audio_condition.notify_all()
