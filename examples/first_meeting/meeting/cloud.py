"""Volcengine cloud calls. Device media is acquired/played only by the SDK."""
from __future__ import annotations

import base64
import asyncio
import io
import json
import uuid
import wave

import httpx

from .config import Settings

TTS_URL = 'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
STT_URL = 'https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash'
LLM_URL = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions'


class CloudError(RuntimeError):
    pass


def pcm_wav(pcm: bytes, rate: int = 16000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return output.getvalue()


class VolcCloud:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=45, follow_redirects=False)

    def headers(self, service: str, resource: str) -> dict:
        app_id = getattr(self.settings, service + '_app_id')
        token = getattr(self.settings, service + '_token')
        if not app_id or not token:
            raise CloudError(f'{service.upper()} 缺少 APP ID 或 Access Token，请先保存配置')
        return {'X-Api-App-Key': app_id, 'X-Api-Access-Key': token,
                'X-Api-Resource-Id': resource, 'X-Api-Request-Id': str(uuid.uuid4())}

    @staticmethod
    def check_http(response: httpx.Response, service: str) -> None:
        if response.status_code >= 400:
            # Do not echo response bodies: providers may include credentials.
            request_id = response.headers.get('x-tt-logid', response.headers.get('x-request-id', ''))
            raise CloudError(f'{service} HTTP {response.status_code}；请检查密钥、模型/音色及资源开通情况。请求 ID: {request_id}')

    async def tts(self, text: str) -> bytes:
        headers = self.headers('tts', self.settings.tts_resource)
        body = {'user': {'uid': 'watcher-first-meeting'}, 'req_params': {
            'text': text, 'speaker': self.settings.tts_voice,
            'audio_params': {'format': 'pcm', 'sample_rate': 24000}}}
        chunks = []
        completed = False
        async with self.client.stream('POST', TTS_URL, headers=headers, json=body) as response:
            self.check_http(response, 'TTS')
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                code = data.get('code')
                if code not in (0, 20000000):
                    raise CloudError(f'TTS 服务错误 {code}；检查音色与资源 ID 是否匹配')
                if data.get('data'):
                    chunks.append(base64.b64decode(data['data'], validate=True))
                if code == 20000000:
                    completed = True
        pcm = b''.join(chunks)
        if not completed or not pcm or len(pcm) % 2:
            raise CloudError('TTS 未返回完整的 PCM 音频')
        return pcm

    async def stt(self, pcm: bytes) -> str:
        if self.settings.stt_mode == 'stream':
            return await self._stream_stt(pcm)
        headers = self.headers('stt', self.settings.stt_resource)
        headers['X-Api-Sequence'] = '-1'
        body = {'user': {'uid': 'watcher-first-meeting'},
                'audio': {'data': base64.b64encode(pcm_wav(pcm)).decode()},
                'request': {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True}}
        response = await self.client.post(STT_URL, headers=headers, json=body)
        self.check_http(response, 'STT')
        code = response.headers.get('X-Api-Status-Code')
        if code == '20000003':
            return ''
        if code != '20000000':
            raise CloudError(f'STT 服务错误 {code}；需要开通 {self.settings.stt_resource}')
        return response.json().get('result', {}).get('text', '').strip()

    async def _stream_stt(self, pcm: bytes) -> str:
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus
        from .speech_protocol import encode_request, decode_response

        headers = self.headers('stt', self.settings.stt_resource)
        headers['X-Api-Connect-Id'] = str(uuid.uuid4())
        config = {'user': {'uid': 'watcher-first-meeting'},
                  'audio': {'format': 'pcm', 'codec': 'raw', 'rate': 16000, 'bits': 16, 'channel': 1},
                  'request': {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True}}
        async def recognize() -> str:
            try:
                async with connect('wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream',
                                   additional_headers=headers, open_timeout=10, close_timeout=2, max_size=4 * 1024 * 1024) as ws:
                    await ws.send(encode_request(json.dumps(config).encode(), 1))
                    decode_response(await ws.recv())
                    async def send_audio():
                        size = 6400
                        for offset in range(0, len(pcm), size):
                            await ws.send(encode_request(pcm[offset:offset + size], 2 + offset // size,
                                          audio=True, final=offset + size >= len(pcm)))
                            await asyncio.sleep(0.01)
                    sender = asyncio.create_task(send_audio())
                    try:
                        while True:
                            text, final = decode_response(await ws.recv())
                            if final:
                                await sender
                                return text.strip()
                    finally:
                        if not sender.done():
                            sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
            except InvalidStatus as error:
                raise CloudError(f'STT HTTP {error.response.status_code}；请确认已开通 {self.settings.stt_resource}') from None
        if not pcm:
            return ''
        return await asyncio.wait_for(recognize(), timeout=45)

    async def chat(self, messages: list[dict]) -> str:
        if not self.settings.llm_key or not self.settings.llm_model:
            raise CloudError('请配置 LLM API Key 和模型 ID / ep 接入点')
        url = ('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
               if self.settings.llm_provider == 'aliyun' else LLM_URL)
        options = {'enable_thinking': False} if self.settings.llm_provider == 'aliyun' else {'thinking': {'type': 'disabled'}}
        response = await self.client.post(url,
            headers={'Authorization': 'Bearer ' + self.settings.llm_key},
            json={'model': self.settings.llm_model, 'messages': messages,
                  'max_tokens': 350, **options})
        self.check_http(response, 'LLM')
        text = response.json()['choices'][0]['message']['content']
        if not isinstance(text, str) or not text.strip():
            raise CloudError('LLM 返回了空回答')
        return text.strip()[:600]

    async def close(self) -> None:
        await self.client.aclose()
