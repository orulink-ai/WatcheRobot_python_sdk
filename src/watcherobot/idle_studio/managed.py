"""Daemon-managed Application lifecycle for the loopback workbench."""
from __future__ import annotations

import asyncio
import os
import socket
import webbrowser

import uvicorn
from watcherobot.application import ApplicationContext
from .server import create_app


async def wait_for_shutdown(app, server, task):
    """Ignore unsupported Desktop frames without forwarding them to Device."""
    try:
        while not task.done() and not app.shutdown_requested:
            try:
                await app.desktop.receive(timeout=0.1)
            except TimeoutError:
                pass
    finally:
        server.should_exit = True
        await task


async def main():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    task = None
    server = None
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        async with ApplicationContext.from_environment() as app:
            server = uvicorn.Server(uvicorn.Config(
                create_app(), host="127.0.0.1", port=port,
                access_log=False, log_level="warning",
            ))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("行为工作台启动失败")
                    if app.shutdown_requested:
                        return
                    await asyncio.sleep(0.02)
                url = f"http://127.0.0.1:{port}"
                app.logger.info("Watcher Idle Studio: %s", url)
                if os.environ.get("WATCHER_IDLE_STUDIO_NO_BROWSER") != "1":
                    await asyncio.to_thread(webbrowser.open, url)
                await wait_for_shutdown(app, server, task)
            finally:
                server.should_exit = True
                await task
    finally:
        listener.close()
