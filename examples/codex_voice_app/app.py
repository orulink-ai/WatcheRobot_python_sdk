"""Launch with watcherobot app run; credentials/channels come from Daemon."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import webbrowser

import uvicorn
from watcherobot.application import ApplicationContext

from service import VoiceService
from continuous_service import ContinuousVoiceService
from web_server import DeviceStatus, create_web_app

ROOT = Path(__file__).resolve().parent


async def desktop_controls(app, service: VoiceService) -> None:
    """Implement microphone hotkeys here, without Daemon content routing."""
    while not app.shutdown_requested:
        try:
            frame = await app.desktop.receive(timeout=.2)
        except TimeoutError:
            continue
        if not isinstance(frame, str):
            continue
        try:
            payload = json.loads(frame)
            kind = payload.get('type')
            if kind == 'ctrl.microphone.open':
                if service.state['connected']:
                    await service.mute(False)
                else:
                    await service.start()
            elif kind == 'ctrl.microphone.close':
                await service.mute(True)
        except (ValueError, AttributeError):
            continue
        except Exception as error:
            service.record_failure(error)
            service.publish()


async def main() -> None:
    web_root = ROOT / 'web/dist'
    if not (web_root / 'index.html').is_file():
        raise RuntimeError('Build the frontend first: cd web && npm run build')
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    try:
        async with ApplicationContext.from_environment() as context:
            status = DeviceStatus(os.environ.get('WATCHER_APP_DEVICE_STATUS_URL', ''))
            service = ContinuousVoiceService(context.robot, status, context.rtc)
            await service.body_feedback.disable()
            server = uvicorn.Server(uvicorn.Config(create_web_app(service, web_root),
                host='127.0.0.1', port=port, access_log=False, log_level='warning'))
            server_task = asyncio.create_task(server.serve(sockets=[listener]))
            desktop_task = asyncio.create_task(desktop_controls(context, service))
            try:
                while not server.started:
                    if server_task.done():
                        await server_task
                        return
                    await asyncio.sleep(.02)
                url = f'http://127.0.0.1:{port}'
                context.logger.info('Codex Voice Lab: %s', url)
                if os.environ.get('WATCHER_VOICE_NO_BROWSER') != '1':
                    await asyncio.to_thread(webbrowser.open, url)
                while not context.shutdown_requested and not server_task.done():
                    await asyncio.sleep(.1)
            finally:
                desktop_task.cancel()
                await asyncio.gather(desktop_task, return_exceptions=True)
                server.should_exit = True
                await server_task
    finally:
        listener.close()


if __name__ == '__main__':
    asyncio.run(main())
