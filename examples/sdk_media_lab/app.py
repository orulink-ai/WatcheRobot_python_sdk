"""Run the standalone, loopback-only SDK Test Bench dashboard."""

from __future__ import annotations

import asyncio
import os
import socket
import webbrowser
from pathlib import Path

import uvicorn

from service import DaemonDeviceStatusProvider, MediaLabService, create_web_app
from watcherobot.application import ApplicationContext


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
            device_status_url = os.environ.get(
                "WATCHER_APP_DEVICE_STATUS_URL",
                "",
            ).strip()
            if not device_status_url:
                raise RuntimeError(
                    "Daemon did not inject WATCHER_APP_DEVICE_STATUS_URL"
                )
            device_manager = DaemonDeviceStatusProvider(device_status_url)
            service = MediaLabService(
                robot=app.robot,
                rtc=app.rtc,
                artifacts_dir=ROOT / "artifacts",
                sample_audio=ROOT / "assets" / "sample_speech.wav",
                device_status_provider=device_manager,
                device_pairer=device_manager.pair,
            )
            web_app = create_web_app(service, web_root=ROOT / "web")
            server = uvicorn.Server(
                uvicorn.Config(
                    web_app,
                    host=HOST,
                    port=port,
                    access_log=False,
                    log_level="warning",
                )
            )
            server_task = asyncio.create_task(
                server.serve(sockets=[listener]),
                name="sdk-test-bench-http",
            )
            while not server.started:
                if server_task.done():
                    await server_task
                await asyncio.sleep(0.02)
            app.logger.info("SDK Test Bench: %s", url)
            if os.environ.get("WATCHER_MEDIA_LAB_NO_BROWSER") != "1":
                await asyncio.to_thread(webbrowser.open, url)
            await server_task
    finally:
        listener.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
