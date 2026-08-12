"""Transport used by a managed Application through Daemon-owned channels."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any

from watcherobot._internal.audio_status import (
    AudioStatusKind,
    classify_audio_status,
)
from watcherobot.errors import CommandError, WatcheRobotError
from watcherobot.protocol import (
    FLAG_FIRST,
    FLAG_LAST,
    FRAME_AUDIO,
    FRAME_VIDEO,
    BinaryFrame,
    build_command,
    build_wspk,
    parse_json_message,
    parse_wspk,
)
from watcherobot.runtime.daemon.application.client import (
    ApplicationCommunicators,
)
from watcherobot.runtime.daemon.application.session import ApplicationChannel

MessageCallback = Callable[[dict[str, Any]], None]
BinaryCallback = Callable[[BinaryFrame], None]
DisconnectCallback = Callable[[], None]
DesktopCallback = Callable[[str | bytes], None]
AUDIO_DEVICE_SLOT_BYTES = 4096
AUDIO_MAX_CREDIT_PACKETS = 8
DEVICE_READY_SYNC_TIMEOUT_SECONDS = 1.0


class DaemonApplicationTransport:
    """Bridge synchronous domain APIs onto one authorized Application session."""

    def __init__(self, *, command_timeout: float = 5.0) -> None:
        self.command_timeout = command_timeout
        self.capabilities: tuple[str, ...] = ()
        self.device_info: dict[str, Any] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._communicators: ApplicationCommunicators | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._started_event = threading.Event()
        self._startup_error: BaseException | None = None
        self._stop_event: asyncio.Event | None = None
        self._message_callback: MessageCallback = lambda _message: None
        self._message_listeners: list[MessageCallback] = []
        self._message_listeners_lock = threading.Lock()
        self._binary_callback: BinaryCallback = lambda _frame: None
        self._disconnect_callback: DisconnectCallback = lambda: None
        self._desktop_callback: DesktopCallback = lambda _frame: None
        self._audio_credit_condition: asyncio.Condition | None = None
        self._audio_flow_stream_id = 0
        self._audio_credits = 0
        self._audio_slots_per_packet = 1
        self._audio_flow_error: str | None = None

    def set_callbacks(
        self,
        message_callback: MessageCallback,
        binary_callback: BinaryCallback,
        disconnect_callback: DisconnectCallback,
    ) -> None:
        self._message_callback = message_callback
        self._binary_callback = binary_callback
        self._disconnect_callback = disconnect_callback

    def set_desktop_callback(self, callback: DesktopCallback) -> None:
        self._desktop_callback = callback

    def add_message_listener(self, callback: MessageCallback) -> None:
        """Observe parsed Device-channel messages without replacing Robot handlers."""

        with self._message_listeners_lock:
            if callback not in self._message_listeners:
                self._message_listeners.append(callback)

    def remove_message_listener(self, callback: MessageCallback) -> None:
        with self._message_listeners_lock:
            if callback in self._message_listeners:
                self._message_listeners.remove(callback)

    def start(self, *, timeout: float = 5.0) -> None:
        if self._thread is not None:
            raise RuntimeError("Application transport is already started")
        self._thread = threading.Thread(
            target=self._thread_main,
            name="watcherobot-application-transport",
            daemon=True,
        )
        self._thread.start()
        if not self._started_event.wait(timeout):
            self.close()
            raise TimeoutError("Timed out connecting Application channels")
        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            raise WatcheRobotError(
                f"Application channels failed to connect: {error}"
            ) from error

    def close(self) -> None:
        loop = self._loop
        stop_event = self._stop_event
        if loop is not None and stop_event is not None and loop.is_running():
            loop.call_soon_threadsafe(stop_event.set)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._thread = None

    def send_command(
        self,
        message_type: str,
        data: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        effective_timeout = (
            self.command_timeout if timeout is None else timeout
        )
        future = self._submit(
            self._send_command(message_type, data, effective_timeout)
        )
        return future.result(timeout=effective_timeout + 1.0)

    def send_command_nowait(
        self,
        message_type: str,
        data: dict[str, Any],
    ) -> Future[dict[str, Any]]:
        return self._submit(
            self._send_command(message_type, data, self.command_timeout)
        )

    def send_audio_stream(
        self,
        pcm: bytes,
        *,
        stream_id: int,
        chunk_bytes: int = 4096,
    ) -> Future[None]:
        if chunk_bytes != AUDIO_DEVICE_SLOT_BYTES:
            raise ValueError(
                f"chunk_bytes must be {AUDIO_DEVICE_SLOT_BYTES} for device audio flow control"
            )
        return self._submit(
            self._send_audio_stream(bytes(pcm), stream_id, chunk_bytes)
        )

    def send_desktop(self, frame: str | bytes) -> Future[None]:
        return self._submit(self._send(ApplicationChannel.DESKTOP, frame))

    def send_device(self, frame: str | bytes) -> Future[None]:
        """Send a protocol frame through the Application's authorized Device channel."""

        return self._submit(self._send_device(frame))

    async def _send_device(self, frame: str | bytes) -> None:
        await self._send(ApplicationChannel.DEVICE, frame)

    def _submit(
        self,
        coroutine: Coroutine[Any, Any, Any],
    ) -> Future[Any]:
        loop = self._loop
        if loop is None or not loop.is_running():
            coroutine.close()
            raise WatcheRobotError("Application transport is not connected")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except BaseException as error:
            self._startup_error = error
            self._started_event.set()
        finally:
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(
                        WatcheRobotError("Application transport disconnected")
                    )
            self._pending.clear()
            pending_tasks = asyncio.all_tasks(loop)
            for task in pending_tasks:
                task.cancel()
            if pending_tasks:
                loop.run_until_complete(
                    asyncio.gather(*pending_tasks, return_exceptions=True)
                )
            loop.close()
            self._loop = None

    async def _run(self) -> None:
        self._stop_event = asyncio.Event()
        communicators = ApplicationCommunicators.from_environment(
            on_frame=self._on_frame,
            on_connected=self._on_channels_connected,
        )
        self._communicators = communicators
        communicator_task = asyncio.create_task(
            communicators.run(),
            name="application-communicators",
        )
        stop_task = asyncio.create_task(
            self._stop_event.wait(),
            name="application-transport-stop",
        )
        done, _pending = await asyncio.wait(
            {communicator_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if communicator_task in done:
            await communicator_task
            if not stop_task.done():
                self._disconnect_callback()
        communicator_task.cancel()
        stop_task.cancel()
        await asyncio.gather(
            communicator_task,
            stop_task,
            return_exceptions=True,
        )
        self._communicators = None

    async def _on_channels_connected(self) -> None:
        """Request a fresh device snapshot after both Application channels attach."""
        try:
            await self._send_command(
                "sys.sdk.ready.get",
                {},
                min(self.command_timeout, DEVICE_READY_SYNC_TIMEOUT_SECONDS),
            )
        except (CommandError, TimeoutError):
            # Older firmware does not implement the snapshot request. Keep
            # Application startup backward-compatible while bounding the wait.
            pass
        finally:
            self._started_event.set()

    async def _send(
        self,
        channel: ApplicationChannel,
        frame: str | bytes,
    ) -> None:
        communicators = self._communicators
        if communicators is None:
            raise WatcheRobotError("Application transport is not connected")
        await communicators.send(channel, frame)

    async def _send_command(
        self,
        message_type: str,
        data: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        command_id = uuid.uuid4().hex
        response_future = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[command_id] = response_future
        try:
            await self._send(
                ApplicationChannel.DEVICE,
                build_command(message_type, data, command_id),
            )
            response = await asyncio.wait_for(
                response_future,
                timeout=timeout,
            )
            if response["type"] == "sys.nack":
                raise CommandError(
                    message_type,
                    str(response.get("data", {}).get("reason", "unknown")),
                )
            return response
        finally:
            self._pending.pop(command_id, None)

    async def _send_audio_stream(
        self,
        pcm: bytes,
        stream_id: int,
        chunk_bytes: int,
    ) -> None:
        if self._audio_credit_condition is None:
            self._audio_credit_condition = asyncio.Condition()
        async with self._audio_credit_condition:
            self._audio_flow_stream_id = stream_id
            self._audio_slots_per_packet = max(
                1,
                (chunk_bytes + AUDIO_DEVICE_SLOT_BYTES - 1)
                // AUDIO_DEVICE_SLOT_BYTES,
            )
            self._audio_credits = 4
            self._audio_flow_error = None
        sequence = 0
        try:
            for offset in range(0, len(pcm), chunk_bytes):
                await self._take_audio_credit(stream_id)
                payload = pcm[offset : offset + chunk_bytes]
                flags = FLAG_FIRST if offset == 0 else 0
                if offset + len(payload) >= len(pcm):
                    flags |= FLAG_LAST
                await self._send(
                    ApplicationChannel.DEVICE,
                    build_wspk(
                        FRAME_AUDIO,
                        flags,
                        stream_id,
                        sequence,
                        payload,
                    ),
                )
                sequence += 1
        finally:
            async with self._audio_credit_condition:
                if self._audio_flow_stream_id == stream_id:
                    self._audio_flow_stream_id = 0
                    self._audio_credits = 0
                    self._audio_slots_per_packet = 1

    async def _take_audio_credit(self, stream_id: int) -> None:
        condition = self._audio_credit_condition
        if condition is None:
            raise WatcheRobotError("audio flow control is not initialized")

        async def wait_for_credit() -> None:
            async with condition:
                await condition.wait_for(
                    lambda: self._audio_flow_stream_id != stream_id
                    or self._audio_credits > 0
                    or self._audio_flow_error is not None
                )
                if self._audio_flow_stream_id != stream_id:
                    raise WatcheRobotError("audio stream was replaced")
                if self._audio_flow_error is not None:
                    raise WatcheRobotError(
                        f"audio stream failed: {self._audio_flow_error}"
                    )
                self._audio_credits -= 1

        try:
            await asyncio.wait_for(
                wait_for_credit(),
                timeout=self.command_timeout,
            )
        except TimeoutError as error:
            raise WatcheRobotError(
                "timed out waiting for robot audio buffer credit"
            ) from error

    async def _update_audio_flow(self, data: dict[str, Any]) -> None:
        condition = self._audio_credit_condition
        if condition is None:
            return
        stream_id = data.get("stream_id")
        reason = data.get("reason", "")
        async with condition:
            if stream_id != self._audio_flow_stream_id:
                return
            if classify_audio_status(reason) is AudioStatusKind.FAILED:
                self._audio_flow_error = str(reason)
            else:
                pending_frames = data.get("pending_frames")
                free_frames = data.get("free_frames")
                queue_depth = data.get("queue_depth")
                start_buffer_frames = data.get("start_buffer_frames")
                playing = data.get("playing") is True
                if (
                    isinstance(pending_frames, int)
                    and pending_frames >= 0
                    and isinstance(queue_depth, int)
                    and queue_depth > 0
                ):
                    target_pending = max(4, queue_depth // 2)
                    if (
                        not playing
                        and isinstance(start_buffer_frames, int)
                        and start_buffer_frames > 0
                    ):
                        target_pending = min(queue_depth, start_buffer_frames)
                    available_slots = max(
                        0,
                        target_pending - pending_frames,
                    )
                    available_packets = (
                        available_slots // self._audio_slots_per_packet
                    )
                    self._audio_credits = min(
                        AUDIO_MAX_CREDIT_PACKETS,
                        available_packets,
                    )
                elif isinstance(free_frames, int) and free_frames > 0:
                    free_packets = (
                        free_frames // self._audio_slots_per_packet
                    )
                    self._audio_credits = min(
                        free_packets,
                        AUDIO_MAX_CREDIT_PACKETS,
                    )
            condition.notify_all()

    async def _on_frame(
        self,
        channel: ApplicationChannel,
        frame: str | bytes,
    ) -> None:
        if channel is ApplicationChannel.DESKTOP:
            self._desktop_callback(frame)
            return
        if isinstance(frame, bytes):
            self._dispatch_binary(frame)
            return
        try:
            preview_payload = json.loads(frame)
        except json.JSONDecodeError:
            preview_payload = None
        if (
            isinstance(preview_payload, dict)
            and preview_payload.get("v") == 1
            and preview_payload.get("kind") == "frame"
        ):
            self._message_callback(
                {
                    "type": "evt.face_tracking.preview.frame",
                    "code": 0,
                    "data": preview_payload,
                }
            )
            return
        message = parse_json_message(frame)
        self._notify_message_listeners(message)
        message_type = message["type"]
        data = message.get("data", {})
        if message_type == "evt.audio.buffer_status":
            await self._update_audio_flow(data)
        if message_type == "evt.sdk.ready":
            capabilities = data.get("capabilities")
            if isinstance(capabilities, list) and all(
                isinstance(item, str) and item for item in capabilities
            ):
                self.capabilities = tuple(capabilities)
            self.device_info.update(data)
        if message_type in {"sys.ack", "sys.nack"}:
            command_id = data.get("command_id")
            future = self._pending.get(command_id)
            if future is not None and not future.done():
                future.set_result(message)
            return
        if message_type == "sys.ping":
            await self._send(
                ApplicationChannel.DEVICE,
                json.dumps(
                    {"type": "sys.pong", "code": 0, "data": data},
                    separators=(",", ":"),
                ),
            )
            return
        self._message_callback(message)

    def _notify_message_listeners(self, message: dict[str, Any]) -> None:
        with self._message_listeners_lock:
            listeners = tuple(self._message_listeners)
        for listener in listeners:
            try:
                listener(message)
            except Exception:
                # Observers must not be able to break transport routing.
                continue

    def _dispatch_binary(self, raw: bytes) -> None:
        if len(raw) >= 12 and raw.startswith(b"FTW1"):
            sequence = int.from_bytes(raw[8:12], "little")
            self._binary_callback(
                BinaryFrame(FRAME_VIDEO, 0, 0, sequence, bytes(raw))
            )
            return
        try:
            frame = parse_wspk(raw)
        except ValueError:
            return
        self._binary_callback(frame)
