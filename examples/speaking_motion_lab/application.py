"""Application lifecycle adapter; the SDK owns Daemon and device connections."""
from __future__ import annotations

import asyncio
import os
import socket
import webbrowser
from logging import Logger
from pathlib import Path
from typing import Protocol

import uvicorn

from web_server import create_web_app


class DesktopInput(Protocol):
    async def receive(self, *, timeout: float | None = None) -> object: ...


class LabContext(Protocol):
    @property
    def shutdown_requested(self) -> bool: ...
    @property
    def desktop(self) -> DesktopInput: ...
    @property
    def logger(self) -> Logger: ...


async def serve_application(context: LabContext, web_root: Path) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_task: asyncio.Task | None = None
    monitor_task: asyncio.Task | None = None
    desktop_task: asyncio.Task | None = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(create_web_app(web_root), host="127.0.0.1", port=port,
                                             access_log=False, log_level="warning"))
        server_task = asyncio.create_task(server.serve(sockets=[listener]), name="behavior-lab-http")
        async def monitor_shutdown() -> None:
            while not context.shutdown_requested:
                await asyncio.sleep(.1)
            server.should_exit = True
        async def drain_desktop() -> None:
            # Unknown Desktop business frames are safely ignored by this preview Application.
            while not context.shutdown_requested:
                try:
                    await context.desktop.receive(timeout=.2)
                except TimeoutError:
                    continue
        monitor_task = asyncio.create_task(monitor_shutdown(), name="behavior-lab-shutdown")
        desktop_task = asyncio.create_task(drain_desktop(), name="behavior-lab-desktop")
        while not server.started:
            if server_task.done():
                await server_task
                return
            await asyncio.sleep(.02)
        url = f"http://127.0.0.1:{port}/joyinside-preview.html"
        context.logger.info("Speaking Motion Lab: %s", url)
        if os.environ.get("WATCHER_BEHAVIOR_LAB_NO_BROWSER") != "1":
            await asyncio.to_thread(webbrowser.open, url)
        await server_task
    finally:
        for task in (monitor_task, desktop_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (monitor_task, desktop_task) if task), return_exceptions=True)
        if server_task is not None and not server_task.done():
            server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(server_task), timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                server_task.cancel()
                await asyncio.gather(server_task, return_exceptions=True)
        listener.close()
