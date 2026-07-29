"""Desktop-side Application channel exposed without device ownership."""

from __future__ import annotations

import asyncio
import queue

from watcherobot.application.transport import DaemonApplicationTransport


class ApplicationDesktop:
    """Send and receive raw desktop frames through the managed Daemon bridge."""

    def __init__(self, transport: DaemonApplicationTransport) -> None:
        self._transport = transport
        self._frames: queue.Queue[str | bytes] = queue.Queue()
        transport.set_desktop_callback(self._frames.put)

    async def send(self, frame: str | bytes) -> None:
        await asyncio.wrap_future(self._transport.send_desktop(frame))

    async def receive(self, *, timeout: float | None = None) -> str | bytes:
        try:
            return await asyncio.to_thread(self._frames.get, True, timeout)
        except queue.Empty as exc:
            raise TimeoutError("Timed out waiting for a desktop frame") from exc
