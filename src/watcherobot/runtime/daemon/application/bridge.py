"""Local WebSocket bridge for one Application runtime session."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import ParamSpec
from urllib.parse import parse_qs, urlencode, urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .session import (
    ApplicationChannel,
    ApplicationSessionRegistry,
    InvalidRunCredentialError,
    SessionOccupiedError,
)


Frame = str | bytes
FrameCallback = Callable[
    [ApplicationChannel, Frame],
    Awaitable[object] | None,
]
ChannelCallback = Callable[[ApplicationChannel], Awaitable[object] | None]
_CallbackParameters = ParamSpec("_CallbackParameters")


class ApplicationBridgeError(RuntimeError):
    """Base error for the local Application communication bridge."""


class ChannelNotConnectedError(ApplicationBridgeError):
    """Raised when a frame targets an unavailable Application channel."""


class ApplicationBridge(ABC):
    """Communication boundary exposed by Daemon runtime management."""

    @abstractmethod
    async def start(self) -> None:
        """Start accepting the current Application's local channels."""

    @abstractmethod
    async def stop(self) -> None:
        """Close all local channels and stop accepting connections."""

    @abstractmethod
    async def send_to_application(
        self,
        channel: ApplicationChannel,
        frame: Frame,
    ) -> None:
        """Send one unchanged frame to a specific Application communicator."""


async def _invoke_callback(
    callback: Callable[_CallbackParameters, Awaitable[object] | None],
    *args: _CallbackParameters.args,
    **kwargs: _CallbackParameters.kwargs,
) -> None:
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        await result


async def _ignore_callback(*_args: object) -> None:
    return None


class LocalWebSocketApplicationBridge(ApplicationBridge):
    """Concrete loopback-only WebSocket bridge for the active run."""

    CLOSE_INVALID_PATH = 4404
    CLOSE_INVALID_CREDENTIAL = 4401
    CLOSE_CHANNEL_OCCUPIED = 4409

    def __init__(
        self,
        *,
        registry: ApplicationSessionRegistry,
        host: str = "127.0.0.1",
        port: int = 0,
        on_frame: FrameCallback = _ignore_callback,
        on_channel_lost: ChannelCallback = _ignore_callback,
    ) -> None:
        self._registry = registry
        self._host = host
        self._requested_port = port
        self._bound_port: int | None = None
        self._on_frame = on_frame
        self._on_channel_lost = on_channel_lost
        self._server: Server | None = None
        self._connections: dict[ApplicationChannel, ServerConnection] = {}
        self._stopping = False

    @property
    def is_running(self) -> bool:
        return self._server is not None

    def set_frame_callback(self, callback: FrameCallback) -> None:
        self._on_frame = callback

    @property
    def bound_port(self) -> int:
        if self._bound_port is None:
            raise ApplicationBridgeError("Application bridge is not running")
        return self._bound_port

    async def start(self) -> None:
        if self._server is not None:
            return
        self._stopping = False
        self._server = await serve(
            self._handle_connection,
            self._host,
            self._requested_port,
            max_size=None,
        )
        sockets = self._server.sockets
        if not sockets:
            await self.stop()
            raise ApplicationBridgeError(
                "Application bridge has no listening socket"
            )
        self._bound_port = int(next(iter(sockets)).getsockname()[1])

    async def stop(self) -> None:
        self._stopping = True
        connections = list(self._connections.values())
        for connection in connections:
            await connection.close(
                code=1001,
                reason="Application bridge stopping",
            )

        server = self._server
        self._server = None
        self._bound_port = None
        if server is not None:
            server.close()
            await server.wait_closed()
        self._connections.clear()

    def channel_url(
        self,
        channel: ApplicationChannel,
        *,
        credential: str,
    ) -> str:
        query = urlencode({"credential": credential})
        return (
            f"ws://{self._host}:{self.bound_port}"
            f"/application/{channel.value}?{query}"
        )

    async def send_to_application(
        self,
        channel: ApplicationChannel,
        frame: Frame,
    ) -> None:
        connection = self._connections.get(channel)
        if connection is None:
            raise ChannelNotConnectedError(
                f"Application channel is not connected: {channel.value}"
            )
        await connection.send(frame)

    async def _handle_connection(
        self,
        connection: ServerConnection,
    ) -> None:
        channel, credential = self._parse_request(connection)
        if channel is None:
            await connection.close(
                code=self.CLOSE_INVALID_PATH,
                reason="invalid Application channel path",
            )
            return

        try:
            self._registry.attach_channel(channel, credential=credential)
        except InvalidRunCredentialError:
            await connection.close(
                code=self.CLOSE_INVALID_CREDENTIAL,
                reason="invalid Application run credential",
            )
            return
        except SessionOccupiedError:
            await connection.close(
                code=self.CLOSE_CHANNEL_OCCUPIED,
                reason="Application channel is already connected",
            )
            return

        self._connections[channel] = connection
        try:
            try:
                async for frame in connection:
                    await _invoke_callback(self._on_frame, channel, frame)
            except ConnectionClosed:
                pass
        finally:
            if self._connections.get(channel) is connection:
                self._connections.pop(channel, None)
                self._registry.detach_channel(
                    channel,
                    abnormal=not self._stopping,
                )
                if not self._stopping:
                    await _invoke_callback(self._on_channel_lost, channel)

    @staticmethod
    def _parse_request(
        connection: ServerConnection,
    ) -> tuple[ApplicationChannel | None, str]:
        request = connection.request
        if request is None:
            return None, ""
        parsed = urlsplit(request.path)
        prefix = "/application/"
        if not parsed.path.startswith(prefix):
            return None, ""
        channel_name = parsed.path.removeprefix(prefix)
        try:
            channel = ApplicationChannel(channel_name)
        except ValueError:
            return None, ""
        credential = parse_qs(parsed.query).get("credential", [""])[0]
        return channel, credential
