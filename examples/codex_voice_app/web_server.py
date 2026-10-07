"""Same-origin, loopback-only browser bridge for a managed Application."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import urllib.parse
import urllib.request

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from service import VoiceService
from errors import BusyRequest, error_message


class DeviceStatus:
    def __init__(self, url: str):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'}
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('Daemon device status URL must be injected loopback HTTP')
        self.url = url

    async def __call__(self) -> bool:
        def read():
            with urllib.request.urlopen(self.url, timeout=1) as response:
                if response.url != self.url:
                    raise ValueError('Unexpected Daemon status redirect')
                payload = json.loads(response.read(65536))
            return payload.get('device', {}).get('online') is True
        return await asyncio.to_thread(read)


def create_web_app(service: VoiceService, web_root: Path) -> FastAPI:
    async def monitor():
        while True:
            await service.refresh_device()
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifespan(_):
        status_task = asyncio.create_task(monitor())
        try:
            yield
        finally:
            status_task.cancel()
            await asyncio.gather(status_task, return_exceptions=True)
            await service.stop(reason='application-shutdown')

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'])

    @app.get('/api/state')
    async def state(request: Request):
        if request.headers.get('sec-fetch-site', 'none') not in {'same-origin', 'none'}:
            raise HTTPException(403, 'Application state is same-origin only')
        return service.snapshot()

    @app.get('/api/photos/{photo_id}')
    async def photo(photo_id: str, request: Request):
        if request.headers.get('sec-fetch-site', 'same-origin') not in {'same-origin', 'none'}:
            raise HTTPException(403, 'Photos are only available in this Application')
        try:
            data = service.robot_tools.photo(photo_id)
        except KeyError:
            raise HTTPException(404, '照片已清除或过期') from None
        return Response(data, media_type='image/jpeg', headers={
            'Cache-Control': 'no-store', 'Cross-Origin-Resource-Policy': 'same-origin',
            'X-Content-Type-Options': 'nosniff'})

    @app.websocket('/api/session')
    async def session(ws: WebSocket):
        if ws.headers.get('origin') != f"http://{ws.headers.get('host')}":
            await ws.close(code=1008)
            return
        await ws.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        service.subscribers.add(queue)
        queue.put_nowait(service.snapshot())

        async def sender():
            while True:
                await ws.send_json({'type': 'snapshot', **await queue.get()})

        async def execute(command):
            try:
                kind = command.get('type')
                if kind == 'start':
                    await service.start()
                elif kind == 'stop':
                    await service.stop()
                elif kind == 'mute' and type(command.get('muted')) is bool:
                    await service.mute(command['muted'])
                elif kind == 'text':
                    await service.text(command.get('text', ''))
                elif kind == 'deviceTest':
                    await service.test_device()
                elif kind == 'permissions':
                    await service.permissions(command.get('permissions'))
                elif kind == 'cancel':
                    await service.cancel_actions()
                elif kind == 'clearPhotos':
                    service.robot_tools.clear_photos()
                else:
                    raise ValueError('不支持的语音控制消息')
            except asyncio.CancelledError:
                raise
            except BusyRequest as error:
                service.state['notice'] = error_message(error)
                service.publish()
            except Exception as error:
                service.record_failure(error)
                service.publish()

        send_task = asyncio.create_task(sender())
        action = None
        controls = set()
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > 20000:
                    await ws.close(code=1009)
                    break
                command = json.loads(raw)
                if not isinstance(command, dict):
                    raise ValueError('Expected an object command')
                if command.get('type') in {'permissions', 'clearPhotos'}:
                    if len(controls) >= 16:
                        service.state['error'] = '设置请求过多，请稍后重试'
                        service.publish()
                    else:
                        control = asyncio.create_task(execute(command))
                        controls.add(control)
                        control.add_done_callback(controls.discard)
                    continue
                if action and not action.done():
                    if command.get('type') not in {'stop', 'cancel'}:
                        service.state['notice'] = '上一项操作仍在进行，追加请求未执行。请等待完成后重试，或先停止。'
                        service.publish()
                        continue
                    if service.stop_task:
                        service.publish()  # Already stopping, acknowledge its current status.
                        continue
                    previous = action
                    action.cancel()
                    controls.add(previous)
                    previous.add_done_callback(controls.discard)
                    # Do not drain startup before issuing the physical stop.
                action = asyncio.create_task(execute(command))
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            send_task.cancel()
            if action:
                action.cancel()
            for task in controls:
                task.cancel()
            await asyncio.gather(send_task, *([action] if action else []), *controls, return_exceptions=True)
            service.subscribers.discard(queue)
            if not service.subscribers:
                await service.stop(reason='browser-disconnected')

    @app.get('/')
    async def index():
        return FileResponse(web_root / 'index.html')

    assets = web_root / 'assets'
    if assets.is_dir():
        app.mount('/assets', StaticFiles(directory=assets), name='assets')
    return app
