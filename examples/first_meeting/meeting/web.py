"""Loopback dashboard with per-process API token and masked configuration."""
from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .cloud import CloudError
from .config import ConfigStore
from .service import MeetingService


def create_web_app(service: MeetingService, store: ConfigStore, web_root: Path,
                   device_status_url: str = '') -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    token = secrets.token_urlsafe(32)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    app.state.device = {'online': False, 'state': 'unknown'}
    app.state.check_task = None

    @app.middleware('http')
    async def protect(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'error': '只允许本机同源页面访问'}, status_code=403)
        if request.url.path.startswith('/api/') and not secrets.compare_digest(request.headers.get('x-meeting-token', ''), token):
            return JSONResponse({'error': '请刷新控制台页面'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    def busy() -> bool:
        check = app.state.check_task
        return service.running or (check is not None and not check.done())

    async def device_status() -> dict:
        if not device_status_url:
            return {'online': False, 'state': 'unavailable'}
        try:
            async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
                response = await client.get(device_status_url)
                response.raise_for_status()
                device = response.json()['device']
            app.state.device = {k: device.get(k) for k in ('online', 'state', 'device_id', 'device_ip')}
        except Exception:
            app.state.device = {'online': False, 'state': 'unavailable'}
        return app.state.device

    app.state.refresh_device = device_status

    @app.get('/')
    async def index():
        return HTMLResponse((web_root / 'index.html').read_text(encoding='utf-8').replace('__API_TOKEN__', token))

    @app.get('/style.css')
    async def style():
        return FileResponse(web_root / 'style.css')

    @app.get('/app.js')
    async def javascript():
        return FileResponse(web_root / 'app.js')

    @app.get('/api/status')
    async def status():
        from watcherobot import __version__
        return {'sdk_version': __version__, 'phase': service.phase, 'running': service.running,
                'name': service.dialogue.name, 'stage': service.dialogue.stage,
                'device': app.state.device, 'checks': service.checks,
                'events': list(service.events), 'photo': service.last_photo,
                'microphone': dict(getattr(service.robot, 'microphone_stats', {})),
                'animations': list(getattr(getattr(getattr(service.robot, 'sdk', None), 'animation', None), 'available_ids', ()))}

    @app.get('/api/config')
    async def config():
        return store.public()

    async def read_body(request: Request) -> dict:
        raw = await request.body()
        if len(raw) > 24000:
            raise ValueError('请求过大')
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('请求必须是 JSON 对象')
        return body

    @app.post('/api/config')
    async def save_config(request: Request):
        if busy():
            return JSONResponse({'error': '请先停止流程并等待云服务检测结束，再保存配置'}, status_code=409)
        try:
            settings = store.update(await read_body(request))
        except Exception:
            # Pydantic validation errors include input values, possibly secrets.
            return JSONResponse({'error': '配置不合法，请检查字段、角度和时间范围'}, status_code=422)
        service.settings = settings
        service.cloud.settings = settings
        service.checks.clear()
        service.log('stage', '配置已保存；密钥字段留空会保留原值')
        return store.public()

    @app.post('/api/start')
    async def start(request: Request):
        if busy():
            return JSONResponse({'error': '已有流程或检测正在运行'}, status_code=409)
        device = await device_status()
        if not device.get('online'):
            return JSONResponse({'error': '机器人未连接，请打开机器人上的 Python SDK 应用并完成配对'}, status_code=409)
        try:
            body = await read_body(request)
            service.start(boot=body.get('boot', True) is not False, gaze_only=body.get('gaze_only') is True)
        except ValueError:
            return JSONResponse({'error': '启动请求无效或已有流程在运行'}, status_code=409)
        return {'ok': True}

    @app.post('/api/stop')
    async def stop():
        service.request_stop()
        return {'ok': True}

    @app.post('/api/text')
    async def text_input(request: Request):
        try:
            body = await read_body(request)
            text = body.get('text', '')
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
                raise ValueError('请输入 1–500 字的内容')
            service.submit(text.strip())
        except ValueError as error:
            return JSONResponse({'error': str(error)}, status_code=409)
        return {'ok': True}

    @app.post('/api/check')
    async def check():
        if busy():
            return JSONResponse({'error': '请先停止正在运行的流程'}, status_code=409)
        async def run_checks():
            # A short synthesized phrase is reused to check real recognition.
            service.checks = {name: {'state': 'waiting'} for name in ('TTS', 'STT', 'LLM')}
            pcm = None
            for name in ('TTS', 'STT', 'LLM'):
                service.checks[name] = {'state': 'checking'}
                try:
                    if name == 'TTS':
                        pcm = await service.cloud.tts('你好呀，人类。')
                        detail = f'合成成功，{len(pcm)} 字节 PCM'
                    elif name == 'STT':
                        if not pcm:
                            raise CloudError('TTS 未通过，无法生成识别测试音频')
                        import av
                        import numpy as np
                        frame = av.AudioFrame.from_ndarray(np.frombuffer(pcm, dtype=np.int16).reshape(1, -1), format='s16', layout='mono')
                        frame.sample_rate = 24000
                        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
                        frames = resampler.resample(frame) + resampler.resample(None)
                        converted = b''.join(f.to_ndarray().tobytes() for f in frames)
                        recognized = await service.cloud.stt(converted)
                        if not recognized:
                            raise CloudError('识别返回空文本')
                        detail = '识别成功：' + recognized
                    else:
                        detail = await service.cloud.chat([{'role': 'user', 'content': '请只回答你好。'}])
                    service.checks[name] = {'state': 'ok', 'detail': detail}
                except Exception as error:
                    detail = str(error) if isinstance(error, CloudError) else type(error).__name__
                    service.checks[name] = {'state': 'error', 'detail': store.redact(detail)}
            service.log('stage', '云服务检测完成，结果见连接状态')
        app.state.check_task = asyncio.create_task(run_checks())
        return {'ok': True}

    @app.post('/api/pair')
    async def pair(request: Request):
        if busy() or not device_status_url:
            return JSONResponse({'error': '请先停止流程'}, status_code=409)
        try:
            body = await read_body(request)
            code = body.get('code', '')
            if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
                raise ValueError('请输入设备当前显示的六位配对码')
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                response = await client.post(device_status_url.rstrip('/') + '/pair',
                    json={'pairing_code': code, 'target_mode': 'python_sdk'})
                response.raise_for_status()
            return {'ok': True}
        except Exception:
            return JSONResponse({'error': '配对失败，请检查六位码、机器人模式和局域网连接'}, status_code=400)

    @app.get('/api/photo')
    async def photo():
        if not service.last_photo:
            return JSONResponse({'error': '尚未拍照'}, status_code=404)
        return FileResponse(service.artifacts / 'photos' / service.last_photo, media_type='image/jpeg')

    return app
