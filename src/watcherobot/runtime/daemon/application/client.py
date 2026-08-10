"""Application-side client for the two Daemon communication channels."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect

from watcherobot.runtime.daemon.application.session import ApplicationChannel

Frame = str | bytes
FrameCallback = Callable[[ApplicationChannel, Frame], Awaitable[None] | None]
ConnectedCallback = Callable[[], Awaitable[None] | None]


async def _ignore_frame(
    _channel: ApplicationChannel,
    _frame: Frame,
) -> None:
    return None


async def _ignore_connected() -> None:
    return None


class ApplicationCommunicators:
    """Connect an Application process to its desktop and device channels."""

    def __init__(
        self,
        *,
        desktop_url: str,
        device_url: str,
        on_frame: FrameCallback = _ignore_frame,
        on_connected: ConnectedCallback = _ignore_connected,
    ) -> None:
        self._desktop_url = desktop_url
        self._device_url = device_url
        self._on_frame = on_frame
        self._on_connected = on_connected
        self._connections: dict[ApplicationChannel, ClientConnection] = {}

    @classmethod
    def from_environment(
        cls,
        *,
        on_frame: FrameCallback = _ignore_frame,
        on_connected: ConnectedCallback = _ignore_connected,
    ) -> "ApplicationCommunicators":
        desktop_url = os.environ.get("WATCHER_APP_DESKTOP_WS_URL", "")
        device_url = os.environ.get("WATCHER_APP_DEVICE_WS_URL", "")
        if not desktop_url or not device_url:
            raise RuntimeError("Application communicator URLs are not configured")
        return cls(
            desktop_url=desktop_url,
            device_url=device_url,
            on_frame=on_frame,
            on_connected=on_connected,
        )

    async def run(self) -> None:
        async with (
            connect(self._desktop_url, max_size=None) as desktop,
            connect(self._device_url, max_size=None) as device,
        ):
            self._connections = {
                ApplicationChannel.DESKTOP: desktop,
                ApplicationChannel.DEVICE: device,
            }
            receive_tasks = (
                asyncio.create_task(
                    self._receive(ApplicationChannel.DESKTOP, desktop),
                    name="application-desktop-receiver",
                ),
                asyncio.create_task(
                    self._receive(ApplicationChannel.DEVICE, device),
                    name="application-device-receiver",
                ),
            )
            try:
                connected_result = self._on_connected()
                if isinstance(connected_result, Awaitable):
                    await connected_result
                await asyncio.gather(*receive_tasks)
            finally:
                for task in receive_tasks:
                    task.cancel()
                await asyncio.gather(*receive_tasks, return_exceptions=True)
                self._connections.clear()

    async def send(
        self,
        channel: ApplicationChannel,
        frame: Frame,
    ) -> None:
        connection = self._connections.get(channel)
        if connection is None:
            raise RuntimeError(
                f"Application communicator is not connected: {channel.value}"
            )
        await connection.send(frame)

    async def _receive(
        self,
        channel: ApplicationChannel,
        connection: ClientConnection,
    ) -> None:
        async for frame in connection:
            result = self._on_frame(channel, frame)
            if isinstance(result, Awaitable):
                await result
