"""Launch the loopback-only Watcher procedural expression workbench."""

from __future__ import annotations

import asyncio
import os
import socket
import webbrowser
from pathlib import Path

import uvicorn

from service import ExpressionLabService, create_web_app
from watcherobot.application import ApplicationContext
from watcherobot.cli import pair_robot


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"


async def main() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    url = f"http://{HOST}:{port}"

    try:
        async with ApplicationContext.from_environment() as app:
            service = ExpressionLabService(robot=app.robot, pair_watcher=pair_robot)
            server = uvicorn.Server(
                uvicorn.Config(
                    create_web_app(
                        service,
                        web_root=ROOT / "web",
                        firmware_root=ROOT / "firmware",
                    ),
                    host=HOST,
                    port=port,
                    access_log=False,
                    log_level="warning",
                )
            )
            server_task = asyncio.create_task(
                server.serve(sockets=[listener]),
                name="expression-lab-http",
            )
            while not server.started:
                if server_task.done():
                    await server_task
                await asyncio.sleep(0.02)
            app.logger.info("Watcher Expression Lab: %s", url)
            if os.environ.get("WATCHER_EXPRESSION_LAB_NO_BROWSER") != "1":
                await asyncio.to_thread(webbrowser.open, url)
            await server_task
    finally:
        listener.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
