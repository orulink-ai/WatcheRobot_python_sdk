"""HTTP provider protocols; transport credentials never enter public errors."""
from __future__ import annotations

import base64
import json
import uuid
from dataclasses import asdict
from typing import Any, AsyncGenerator, AsyncIterator, Sequence
from urllib.parse import quote

import httpx

from ..configuration import ModelConfig
from ..contracts import AudioChunk, Message, VoiceError


class HTTPProvider:
    def __init__(self, config: ModelConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._owned = client is None
        self.client = client or httpx.AsyncClient(timeout=config.timeout, follow_redirects=False)

    async def close(self) -> None:
        if self._owned:
            await self.client.aclose()

    def endpoint(self, default: str, path: str = '') -> str:
        return (self.config.base_url or default).rstrip('/') + path

    def bearer(self) -> dict[str, str]:
        key = self.config.credentials.get('api_key', '')
        return {'Authorization': 'Bearer ' + key} if key else {}

    @staticmethod
    def check(response: httpx.Response, stage: str) -> None:
        if not 200 <= response.status_code < 300:
            raise VoiceError(f'{stage} HTTP {response.status_code}；请检查凭据、模型与资源权限')

    def volc_headers(self) -> dict[str, str]:
        return {'X-Api-App-Key': self.config.credentials.get('app_id', ''),
                'X-Api-Access-Key': self.config.credentials.get('access_token', ''),
                'X-Api-Resource-Id': self.config.resource_id,
                'X-Api-Request-Id': str(uuid.uuid4())}


class ChatLLM(HTTPProvider):
    async def generate(self, messages: Sequence[Message]) -> AsyncGenerator[str, None]:
        default = ('https://ark.cn-beijing.volces.com/api/v3' if self.config.provider == 'ark'
                   else 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        body = {**self.config.options, **self.config.parameters,
                'model': self.config.model, 'messages': [asdict(m) for m in messages], 'stream': True}
        if self.config.provider == 'qwen':
            body.setdefault('enable_thinking', False)
        try:
            async with self.client.stream('POST', self.endpoint(default, '/chat/completions'),
                                          headers=self.bearer(), json=body) as response:
                self.check(response, 'LLM')
                finished = False
                async for line in response.aiter_lines():
                    if not line.startswith('data:'):
                        continue
                    payload = line[5:].strip()
                    if payload == '[DONE]':
                        finished = True
                        break
                    data = json.loads(payload)
                    if 'error' in data:
                        raise VoiceError('LLM 服务返回错误')
                    for choice in data.get('choices', []):
                        content = choice.get('delta', {}).get('content')
                        if content:
                            if not isinstance(content, str):
                                raise VoiceError('LLM 文本响应格式无效')
                            yield content
                if not finished:
                    raise VoiceError('LLM 流提前结束')
        except VoiceError:
            raise
        except Exception:
            raise VoiceError('LLM 请求失败或响应无效；请检查网络及模型协议') from None


class CloudTTS(HTTPProvider):
    """Each request produces raw mono S16LE at 24 kHz, never WAV headers."""

    def request(self, text: str) -> tuple[str, dict[str, str], dict[str, Any], dict[str, Any]]:
        c = self.config
        extra = {**c.options, **c.parameters}
        headers = self.bearer()
        params: dict[str, Any] = {}
        body: dict[str, Any]
        if c.provider == 'volcengine':
            url = self.endpoint('https://openspeech.bytedance.com/api/v3/tts/unidirectional')
            headers = self.volc_headers()
            body = {'user': {'uid': 'watcherobot'}, 'req_params': {
                **extra, 'text': text, 'speaker': c.voice_id,
                'audio_params': {**extra.get('audio_params', {}), 'format': 'pcm', 'sample_rate': 24000}}}
        elif c.provider == 'deepgram':
            url = self.endpoint('https://api.deepgram.com/v1', '/speak')
            headers = {'Authorization': 'Token ' + c.credentials.get('api_key', '')}
            params = {**extra, 'model': c.model or c.voice_id, 'encoding': 'linear16',
                      'sample_rate': 24000, 'container': 'none'}
            body = {'text': text}
        elif c.provider == 'cartesia':
            url = self.endpoint('https://api.cartesia.ai', '/tts/bytes')
            headers['Cartesia-Version'] = str(extra.pop('api_version', '2026-08-14'))
            body = {**extra, 'model_id': c.model, 'transcript': text, 'voice': c.voice_id,
                    'output_format': {'container': 'raw', 'encoding': 'pcm_s16le', 'sample_rate': 24000}}
            if c.language:
                body['language'] = c.language
        elif c.provider == 'elevenlabs':
            url = self.endpoint('https://api.elevenlabs.io/v1', '/text-to-speech/' + quote(c.voice_id, safe='') + '/stream')
            headers = {'xi-api-key': c.credentials.get('api_key', '')}
            params = {'output_format': 'pcm_24000'}
            body = {**extra, 'model_id': c.model, 'text': text}
            if c.language:
                body['language_code'] = c.language
        elif c.provider == 'minimaxi':
            url = self.endpoint('https://api.minimax.cn/v1', '/t2a_v2')
            body = {**extra, 'model': c.model, 'text': text, 'stream': False,
                    'voice_setting': {**extra.get('voice_setting', {}), 'voice_id': c.voice_id},
                    'audio_setting': {'sample_rate': 24000, 'format': 'pcm', 'channel': 1},
                    'output_format': 'hex'}
            if c.language:
                body['language_boost'] = c.language
        else:
            raise VoiceError('TTS 未注册供应商')
        return url, headers, params, body

    async def synthesize(self, text: str) -> AsyncGenerator[AudioChunk, None]:
        url, headers, params, body = self.request(text)
        try:
            async with self.client.stream('POST', url, headers=headers, params=params, json=body) as response:
                self.check(response, 'TTS')
                if self.config.provider == 'volcengine':
                    complete = False
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get('code') not in (0, 20000000):
                            raise VoiceError('TTS 火山服务错误；请检查音色与资源权限')
                        if data.get('data'):
                            yield AudioChunk(base64.b64decode(data['data'], validate=True))
                        if data.get('code') == 20000000:
                            complete = True
                    if not complete:
                        raise VoiceError('TTS 流提前结束')
                elif self.config.provider == 'minimaxi':
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > 10 * 1024 * 1024:
                            raise VoiceError('TTS 响应超过大小限制')
                    data = json.loads(raw)
                    if data.get('base_resp', {}).get('status_code') != 0:
                        raise VoiceError('TTS MiniMax 服务错误；请检查模型与音色权限')
                    yield AudioChunk(bytes.fromhex(data['data']['audio']))
                else:
                    async for part in response.aiter_bytes(chunk_size=8192):
                        yield AudioChunk(part)
        except VoiceError:
            raise
        except Exception:
            raise VoiceError('TTS 请求失败或响应无效；请检查网络及合成协议') from None
