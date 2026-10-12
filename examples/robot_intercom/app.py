"""Run Robot Intercom as a Daemon-managed Application."""
from __future__ import annotations

import asyncio
import os
import socket
import webbrowser
from pathlib import Path

import uvicorn
from watcherobot.application import ApplicationContext
from service import DeviceStatus, IntercomService, create_web_app

ROOT = Path(__file__).resolve().parent


async def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        async with ApplicationContext.from_environment() as context:
            status_url = os.environ.get("WATCHER_APP_DEVICE_STATUS_URL", "").strip()
            if not status_url:
                raise RuntimeError("Daemon did not inject the device management URL")
            data_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
            service = IntercomService(robot=context.robot, rtc=context.rtc,
                                     device_status=DeviceStatus(status_url),
                                     artifacts_dir=data_root / "WatcheRobot" / "robot-intercom" / "photos")
            server = uvicorn.Server(uvicorn.Config(create_web_app(service, web_root=ROOT / "web"),
                                                  host="127.0.0.1", port=port, access_log=False))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                while not server.started:
                    if task.done():
                        await task
                        return
                    await asyncio.sleep(0.05)
                url = f"http://127.0.0.1:{port}"
                context.logger.info("机器人对讲 / Robot Intercom: %s", url)
                if os.environ.get("WATCHER_INTERCOM_NO_BROWSER") != "1":
                    await asyncio.to_thread(webbrowser.open, url)
                while not task.done() and not context.shutdown_requested:
                    await asyncio.sleep(0.1)
            finally:
                service.request_shutdown()
                server.should_exit = True
                await task  # Drain HTTP photos and lifespan before closing SDK transport.


if __name__ == "__main__":
    asyncio.run(main())
