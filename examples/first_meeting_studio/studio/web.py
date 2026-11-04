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
    app.state.operation_lock = asyncio.Lock()
    device_client_task = None

    async def device_client():
        nonlocal device_client_task
        if device_client_task is None:
            # TLS setup can take hundreds of milliseconds on Windows, even for
            # a local HTTP URL. Keep it off the motion loop and reuse the pool.
            device_client_task = asyncio.create_task(asyncio.to_thread(
                httpx.AsyncClient, timeout=1.5, trust_env=False))
        return await asyncio.shield(device_client_task)

    async def close_device_client():
        if device_client_task is not None:
            client = await device_client_task
            await client.aclose()

    app.state.close_device_client = close_device_client

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
            client = await device_client()
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

    @app.get('/request-error.mjs')
    async def request_error_helper():
        return FileResponse(web_root / 'request-error.mjs', media_type='text/javascript')

    @app.get('/provisioning/{stage}.png')
    async def provisioning_preview(stage: str):
        if stage not in ('scan', 'network', 'failed'):
            return JSONResponse({'error': '未知页面'}, status_code=404)
        return FileResponse(web_root.parent / 'assets' / 'provisioning' / (stage + '.png'))

    @app.get('/api/status')
    async def status():
        from watcherobot import __version__
        from .scenes import EXPRESSIONS
        sdk = getattr(service.robot, 'sdk', None)
        return {'sdk_version': __version__, 'phase': service.phase, 'running': service.running,
                'firmware_version': sdk.device_info.get('firmware_version') if sdk else None,
                'device_capabilities': list(sdk.device_info.get('capabilities', ())) if sdk else [],
                'resources': sdk.resource_snapshot if sdk and hasattr(sdk, 'resource_snapshot') else {},
                'last_error': next((event['text'] for event in reversed(service.events) if event['kind'] == 'error'), '') if service.phase in ('error', 'cleanup_error') else '',
                'expression_source': 'ESP32 anim_service authored presets',
                'authored_expressions_supported': bool(sdk and sdk.supports('expression.presets.authored.v1')),
                'seamless_photo_supported': bool(sdk and sdk.supports('camera.capture.seamless_feedback.v1')),
                'expression_presets': {name: value['preset'] for name, value in EXPRESSIONS.items()},
                'name': service.dialogue.name, 'stage': service.dialogue.stage,
                'device': app.state.device, 'checks': service.checks,
                'completed': bool(service.memory.get(service.device_id)),
                'rehearsal': service.rehearsal,
                'onboarding': service.onboarding.snapshot(),
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
        body = await read_body(request)
        async with app.state.operation_lock:
            if busy():
                return JSONResponse({'error': '请先停止流程并等待云服务检测结束，再保存配置'}, status_code=409)
            try:
                settings = store.update(body)
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
        body = await read_body(request)
        async with app.state.operation_lock:
            if busy():
                return JSONResponse({'error': '已有流程或检测正在运行'}, status_code=409)
            if not (body.get('connection_probe') is True or body.get('gaze_only') is True):
                if service.settings.missing_speech_fields():
                    return JSONResponse({'error': '请先在声音与聆听中填写语音凭据并检测服务；也可以先运行连接与苏醒短测'}, status_code=409)
            device = await device_status()
            if not device.get('online'):
                return JSONResponse({'error': '机器人未连接，请打开机器人上的 Python SDK 应用并完成配对'}, status_code=409)
            if busy():
                return JSONResponse({'error': '已有流程或检测正在运行'}, status_code=409)
            try:
                info = service.robot.sdk.device_info
                service.device_id = str(info.get("device_id") or info.get("mac") or "")
                service.start(boot=body.get('boot', True) is not False, gaze_only=body.get('gaze_only') is True, rehearsal=body.get("rehearsal") is True, onboarding=True, connection_probe=body.get('connection_probe') is True)
            except ValueError as error:
                return JSONResponse({'error': str(error)}, status_code=409)
            return {'ok': True}

    @app.post('/api/onboarding/next')
    async def onboarding_next(request: Request):
        body = await read_body(request)
        async with app.state.operation_lock:
            device = await device_status()
            if not device.get('online'):
                service.request_stop()
                return JSONResponse({'error': '真实设备已断开，请重连后重新开始'}, status_code=409)
            try:
                if not service.running or service.stopped.is_set():
                    raise ValueError('流程已停止，请重新开始')
                if type(body.get('revision')) is not int:
                    raise ValueError('请刷新页面后重试当前步骤')
                service.onboarding.advance(body.get('run_id'), body.get('stage'), body.get('action', 'next'), body['revision'])
            except ValueError as error:
                return JSONResponse({'error': str(error)}, status_code=409)
            return {'ok': True}

    @app.post('/api/stop')
    async def stop():
        await service.stop()
        if service.cleanup_required:
            return JSONResponse({'error': '设备停止尚未确认，请再次停止以重试清理'}, status_code=503)
        return {'ok': True}

    @app.post('/api/config/clear-credentials')
    async def clear_credentials():
        async with app.state.operation_lock:
            if busy() or service.cleanup_required:
                return JSONResponse({'error': '请先停止流程并完成清理和检测'}, status_code=409)
            service.settings = store.clear_credentials()
            service.cloud.settings = service.settings
            service.checks.clear()
            return store.public()

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
        async with app.state.operation_lock:
            if busy():
                return JSONResponse({'error': '请先停止正在运行的流程'}, status_code=409)
            app.state.check_task = asyncio.create_task(run_checks())
            return {'ok': True}

    @app.post("/api/audition")
    async def audition():
        from fastapi.responses import Response
        from .cloud import pcm_wav
        async with app.state.operation_lock:
            if busy():
                return JSONResponse({"error": "请先停止流程和检测"}, status_code=409)
            async def synthesize():
                pcm = await service.cloud.tts("啊，找到你了！我是 Watcher。以后这张桌子，分我一小块就好。")
                return pcm_wav(pcm, 24000)
            app.state.check_task = asyncio.create_task(synthesize())
        try:
            return Response(await app.state.check_task, media_type="audio/wav")
        except Exception as error:
            return JSONResponse({"error": service.error_detail(error)}, status_code=503)

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
        path = service.artifacts / "photos" / service.last_photo
        if not service.last_photo or not path.is_file():
            return JSONResponse({'error': '尚未拍照'}, status_code=404)
        return FileResponse(path, media_type='image/jpeg')

    @app.post("/api/photo")
    async def capture_photo():
        async with app.state.operation_lock:
            if busy() or service.cleanup_required:
                return JSONResponse({"error": "请先停止当前流程，再单独调试拍照"}, status_code=409)
            if not service.device_id:
                return JSONResponse({"error": "请先连接 Watcher"}, status_code=409)
            async def capture():
                import uuid
                path = service.artifacts / "photos" / (uuid.uuid4().hex + ".jpg")
                service.phase = "capturing"
                try:
                    await service.robot.photo(path)
                    service.check_stop()
                    service.last_photo = path.name
                    service.log("stage", "照片已保存，可在初见合影中预览")
                    service.phase = "ready"
                except InterruptedError:
                    service.phase = "stopped"
                except Exception as error:
                    service.log("error", "拍照未成功：" + service.error_detail(error))
                    service.phase = "error"
            service.stopped.clear()
            service.task = asyncio.create_task(capture())
            return {"ok": True}

    return app
