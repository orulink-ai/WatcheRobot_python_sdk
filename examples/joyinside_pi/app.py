"""Daemon-managed webpage; robot calls use the injected Device channel."""
from __future__ import annotations
import asyncio
import os
import socket
import webbrowser
from pathlib import Path
import uvicorn
from watcherobot.application import ApplicationContext
from service import ConversationService, create_web_app
from connection import DaemonConnection
from speech_preferences import load_backend, save_backend
from watcherobot.voice.configuration import default_credential_directory

ROOT = Path(__file__).resolve().parent

async def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        async with ApplicationContext.from_environment() as context:
            preferences = default_credential_directory(ROOT, context.app_id, installed=True).parent / "speech-settings.json"
            service = ConversationService(context, backend_name=load_backend(preferences),
                                          save_backend=lambda name: save_backend(preferences, name))
            connection = DaemonConnection(os.environ.get("WATCHER_APP_DEVICE_STATUS_URL", ""))
            if os.environ.get("WATCHER_SPEECH_AUTOSTART") == "1" or os.environ.get("WATCHER_SPEECH_TEST_TEXT"):
                service.start()
            server = uvicorn.Server(uvicorn.Config(create_web_app(service, ROOT / "web", connection=connection),
                host="127.0.0.1", port=port, access_log=False, log_level="warning"))
            http_task = asyncio.create_task(server.serve(sockets=[listener]))
            runner = asyncio.create_task(service.run())
            controls = asyncio.create_task(service.desktop_controls())
            try:
                while not server.started:
                    if http_task.done():
                        await http_task
                        return
                    await asyncio.sleep(.02)
                url = f"http://127.0.0.1:{port}"
                context.logger.info("JoyInside Pi 对话页面：%s", url)
                if os.environ.get("WATCHER_SPEECH_NO_BROWSER") != "1":
                    await asyncio.to_thread(webbrowser.open, url)
                while not context.shutdown_requested and not http_task.done() and not runner.done():
                    await asyncio.sleep(.1)
                if runner.done(): await runner
            finally:
                service.shutdown()
                controls.cancel()
                await asyncio.gather(controls, return_exceptions=True)
                try:
                    await runner
                finally:
                    server.should_exit = True
                    await http_task

if __name__ == "__main__":
    asyncio.run(main())
