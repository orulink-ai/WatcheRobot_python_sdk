from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from typing import Any, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from ._internal.audio_status import AudioStatusKind, classify_audio_status
from .errors import AuthenticationError, CommandError, ConnectionTimeoutError, WatcheRobotError
from .protocol import (
    DISCOVERY_PORT,
    PROTOCOL_VERSION,
    WEBSOCKET_PORT,
    BinaryFrame,
    ProtocolError,
    build_command,
    build_wspk,
    FLAG_FIRST,
    FLAG_LAST,
    FRAME_AUDIO,
    parse_json_message,
    parse_wspk,
)

MessageCallback = Callable[[dict[str, Any]], None]
BinaryCallback = Callable[[BinaryFrame], None]
DisconnectCallback = Callable[[], None]


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, websocket_port: int) -> None:
        self.websocket_port = websocket_port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            probe = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if (
            not isinstance(probe, dict)
            or probe.get("cmd") != "SDK_DISCOVER"
            or probe.get("service") != "watcher-sdk"
            or probe.get("protocol_version") != PROTOCOL_VERSION
            or self.transport is None
        ):
            return
        announce = {
            "cmd": "ANNOUNCE",
            "service": "watcher-sdk",
            "protocol_version": PROTOCOL_VERSION,
            "port": self.websocket_port,
            "server": "watcherobot-python-sdk",
        }
        self.transport.sendto(json.dumps(announce, separators=(",", ":")).encode("utf-8"), addr)


class BackgroundTransport:
    def __init__(
        self,
        *,
        discovery_port: int = DISCOVERY_PORT,
        websocket_port: int = WEBSOCKET_PORT,
        host: str = "0.0.0.0",
        command_timeout: float = 5.0,
    ) -> None:
        self.discovery_port = discovery_port
        self.websocket_port = websocket_port
        self.host = host
        self.command_timeout = command_timeout
        self.capabilities: tuple[str, ...] = ()
        self.device_info: dict[str, Any] = {}
        self._pairing_code = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._websocket: Any = None
        self._pending: dict[str, asyncio.Future] = {}
        self._started_event = threading.Event()
        self._ready_event = threading.Event()
        self._startup_error: BaseException | None = None
        self._async_stop: asyncio.Event | None = None
        self._message_callback: MessageCallback = lambda message: None
        self._binary_callback: BinaryCallback = lambda frame: None
        self._disconnect_callback: DisconnectCallback = lambda: None
        self._was_ready = False
        self._audio_credit_condition: asyncio.Condition | None = None
        self._audio_flow_stream_id = 0
        self._audio_credits = 0
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

    def start(self, pairing_code: str, timeout: float = 15.0) -> None:
        if not re.fullmatch(r"\d{6}", pairing_code):
            raise ValueError("pairing_code must contain exactly six digits")
        if self._thread is not None:
            raise RuntimeError("transport is already started")
        self._pairing_code = pairing_code
        self._thread = threading.Thread(target=self._thread_main, name="watcherobot-gateway", daemon=True)
        self._thread.start()
        if not self._started_event.wait(min(timeout, 5.0)):
            self.close()
            raise ConnectionTimeoutError("Python SDK gateway did not start")
        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            raise WatcheRobotError(f"Python SDK gateway failed to start: {error}") from error
        if not self._ready_event.wait(timeout):
            self.close()
            raise ConnectionTimeoutError(
                "No robot connected. Open sdk.control.app and use the displayed pairing code."
            )
        if self._startup_error is not None:
            error = self._startup_error
            self.close()
            if isinstance(error, WatcheRobotError):
                raise error
            raise WatcheRobotError(str(error)) from error

    def send_command(self, message_type: str, data: dict, timeout: float | None = None) -> dict:
        if self._loop is None or self._websocket is None or not self._ready_event.is_set():
            raise WatcheRobotError("robot is not connected")
        future = asyncio.run_coroutine_threadsafe(self._send_command(message_type, data, timeout), self._loop)
        return future.result(timeout=(timeout or self.command_timeout) + 1.0)

    def send_audio_stream(
        self,
        pcm: bytes,
        *,
        stream_id: int,
        chunk_bytes: int = 4096,
    ):
        if self._loop is None or self._websocket is None or not self._ready_event.is_set():
            raise WatcheRobotError("robot is not connected")
        if chunk_bytes <= 0 or chunk_bytes > 32768 or chunk_bytes % 2 != 0:
            raise ValueError("chunk_bytes must be an even value between 2 and 32768")
        return asyncio.run_coroutine_threadsafe(
            self._send_audio_stream(bytes(pcm), stream_id, chunk_bytes),
            self._loop,
        )

    def close(self) -> None:
        loop = self._loop
        async_stop = self._async_stop
        if loop is not None and async_stop is not None and loop.is_running():
            loop.call_soon_threadsafe(async_stop.set)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._thread = None

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except BaseException as error:
            self._startup_error = error
            self._started_event.set()
            self._ready_event.set()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self._loop = None

    async def _run(self) -> None:
        self._async_stop = asyncio.Event()
        websocket_server = await websockets.serve(
            self._handle_websocket,
            self.host,
            self.websocket_port,
            ping_interval=20,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
        )
        bound_port = websocket_server.sockets[0].getsockname()[1]
        self.websocket_port = bound_port
        discovery_transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: DiscoveryProtocol(bound_port),
            local_addr=(self.host, self.discovery_port),
            allow_broadcast=True,
        )
        self._started_event.set()
        try:
            await self._async_stop.wait()
        finally:
            websocket = self._websocket
            if websocket is not None:
                await websocket.close(code=1001, reason="SDK client closed")
            discovery_transport.close()
            websocket_server.close()
            await websocket_server.wait_closed()

    async def _handle_websocket(self, websocket: Any) -> None:
        if self._websocket is not None:
            await websocket.close(code=1013, reason="another robot session is active")
            return
        self._websocket = websocket
        ready = False
        try:
            try:
                raw_hello = await asyncio.wait_for(websocket.recv(), timeout=self.command_timeout)
            except asyncio.TimeoutError as error:
                raise ConnectionTimeoutError("Timed out waiting for the robot hello message") from error
            if not isinstance(raw_hello, str):
                raise ProtocolError("expected sys.client.hello text frame")
            hello = parse_json_message(raw_hello)
            if hello["type"] != "sys.client.hello":
                raise ProtocolError("first message must be sys.client.hello")
            hello_data = hello.get("data", {})
            self.device_info = {
                "device_id": hello_data.get("device_id", "unknown"),
                "firmware_version": hello_data.get("fw_version", "unknown"),
                "mac": hello_data.get("mac", ""),
            }
            await websocket.send(
                json.dumps(
                    {"type": "sys.ack", "code": 0, "data": {"type": "sys.client.hello"}},
                    separators=(",", ":"),
                )
            )

            auth_id = uuid.uuid4().hex
            await websocket.send(
                build_command(
                    "sys.sdk.authenticate",
                    {
                        "pairing_code": self._pairing_code,
                        "protocol_version": PROTOCOL_VERSION,
                        "client_name": "watcherobot-python-sdk",
                    },
                    auth_id,
                )
            )
            authenticated = False
            announced_ready = False
            while not (authenticated and announced_ready):
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=self.command_timeout)
                except asyncio.TimeoutError as error:
                    raise ConnectionTimeoutError(
                        "Timed out waiting for the robot SDK authentication response"
                    ) from error
                if isinstance(raw, bytes):
                    self._dispatch_binary(raw)
                    continue
                message = parse_json_message(raw)
                data = message.get("data", {})
                if message["type"] == "sys.nack" and data.get("command_id") == auth_id:
                    raise AuthenticationError(data.get("reason", "authentication_failed"))
                if message["type"] == "sys.ack" and data.get("command_id") == auth_id:
                    authenticated = True
                elif message["type"] == "evt.sdk.ready":
                    if data.get("protocol_version") != PROTOCOL_VERSION:
                        raise AuthenticationError("protocol_version_mismatch")
                    self.capabilities = tuple(data.get("capabilities", ()))
                    self.device_info.update(data)
                    announced_ready = True

            ready = True
            self._was_ready = True
            self._ready_event.set()
            async for raw in websocket:
                if isinstance(raw, bytes):
                    self._dispatch_binary(raw)
                else:
                    await self._dispatch_message(parse_json_message(raw))
        except (ConnectionClosed, asyncio.CancelledError):
            pass
        except BaseException as error:
            if not ready:
                self._startup_error = error
                self._ready_event.set()
        finally:
            if self._websocket is websocket:
                self._websocket = None
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(WatcheRobotError("robot disconnected"))
            self._pending.clear()
            if ready:
                self._disconnect_callback()

    async def _send_command(self, message_type: str, data: dict, timeout: float | None) -> dict:
        websocket = self._websocket
        if websocket is None:
            raise WatcheRobotError("robot is not connected")
        command_id = uuid.uuid4().hex
        response_future = asyncio.get_running_loop().create_future()
        self._pending[command_id] = response_future
        try:
            await websocket.send(build_command(message_type, data, command_id))
            response = await asyncio.wait_for(response_future, timeout=timeout or self.command_timeout)
        finally:
            self._pending.pop(command_id, None)
        if response["type"] == "sys.nack":
            raise CommandError(message_type, response.get("data", {}).get("reason", "unknown"))
        return response

    async def _send_audio_stream(self, pcm: bytes, stream_id: int, chunk_bytes: int) -> None:
        websocket = self._websocket
        if websocket is None:
            raise WatcheRobotError("robot is not connected")
        if self._audio_credit_condition is None:
            self._audio_credit_condition = asyncio.Condition()
        async with self._audio_credit_condition:
            self._audio_flow_stream_id = stream_id
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
                await websocket.send(build_wspk(FRAME_AUDIO, flags, stream_id, sequence, payload))
                sequence += 1
        finally:
            async with self._audio_credit_condition:
                if self._audio_flow_stream_id == stream_id:
                    self._audio_flow_stream_id = 0
                    self._audio_credits = 0

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
                    raise WatcheRobotError(f"audio stream failed: {self._audio_flow_error}")
                self._audio_credits -= 1

        try:
            await asyncio.wait_for(wait_for_credit(), timeout=self.command_timeout)
        except asyncio.TimeoutError as error:
            raise WatcheRobotError("timed out waiting for robot audio buffer credit") from error

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
                self._audio_flow_error = reason
            else:
                pending_frames = data.get("pending_frames")
                free_frames = data.get("free_frames")
                queue_depth = data.get("queue_depth")
                if (
                    isinstance(pending_frames, int)
                    and pending_frames >= 0
                    and isinstance(queue_depth, int)
                    and queue_depth > 0
                ):
                    target_pending = max(4, queue_depth // 2)
                    available_slots = max(0, target_pending - pending_frames)
                    # One 4096-byte WSPK packet is delivered to the ESP-IDF
                    # callback in roughly three 2 KiB fragments. Convert the
                    # device's internal-slot credit back to host packets.
                    self._audio_credits = min(8, (available_slots + 2) // 3)
                elif isinstance(free_frames, int) and free_frames > 0:
                    self._audio_credits = min(free_frames, 8)
            condition.notify_all()

    async def _dispatch_message(self, message: dict[str, Any]) -> None:
        message_type = message["type"]
        data = message.get("data", {})
        if message_type == "evt.audio.buffer_status":
            await self._update_audio_flow(data)
        if message_type in ("sys.ack", "sys.nack"):
            command_id = data.get("command_id")
            future = self._pending.get(command_id)
            if future is not None and not future.done():
                future.set_result(message)
            return
        if message_type == "sys.ping" and self._websocket is not None:
            await self._websocket.send(json.dumps({"type": "sys.pong", "code": 0, "data": data}))
            return
        self._message_callback(message)

    def _dispatch_binary(self, raw: bytes) -> None:
        try:
            frame = parse_wspk(raw)
        except ProtocolError:
            return
        self._binary_callback(frame)
