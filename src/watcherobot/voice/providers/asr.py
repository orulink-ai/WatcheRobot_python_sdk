"""Concurrent audio upload and transcription, with explicit end-of-input."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, AsyncIterator, Callable
from urllib.parse import quote, urlencode

from websockets.asyncio.client import connect

from ..configuration import ModelConfig
from ..contracts import Transcript, VoiceError
from .http import HTTPProvider
from .volc_protocol import decode_response, encode_request


class StreamingASR(HTTPProvider):
    def __init__(self, config: ModelConfig, *, connector: Callable[..., Any] = connect, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self.connector = connector
        self._token = ''
        self._token_expiry = 0.0

    async def _aliyun_token(self) -> str:
        c = self.config
        if c.credentials.get('token'):
            return c.credentials['token']
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        params = {'AccessKeyId': c.credentials['access_key_id'], 'Action': 'CreateToken',
                  'Format': 'JSON', 'RegionId': 'cn-shanghai', 'SignatureMethod': 'HMAC-SHA1',
                  'SignatureNonce': uuid.uuid4().hex, 'SignatureVersion': '1.0',
                  'Timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'Version': '2019-02-28'}
        canonical = urlencode(sorted(params.items()), quote_via=quote, safe='~')
        signing = 'GET&%2F&' + quote(canonical, safe='~')
        digest = hmac.new((c.credentials['access_key_secret'] + '&').encode(), signing.encode(), hashlib.sha1).digest()
        params['Signature'] = base64.b64encode(digest).decode()
        response = await self.client.get('https://nls-meta.cn-shanghai.aliyuncs.com/', params=params)
        self.check(response, 'ASR Token')
        token = response.json()['Token']
        self._token = str(token['Id'])
        self._token_expiry = float(token['ExpireTime'])
        return self._token

    async def transcribe(self, audio: AsyncIterator[bytes]) -> AsyncGenerator[Transcript, None]:
        c = self.config
        sender: asyncio.Task[None] | None = None
        receiver: asyncio.Task[Any] | None = None
        try:
            headers: dict[str, str] = {}
            if c.provider == 'volcengine':
                url = c.base_url or 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel'
                headers = self.volc_headers()
                headers['X-Api-Connect-Id'] = str(uuid.uuid4())
            elif c.provider == 'deepgram':
                params = {**c.options, **c.parameters, 'model': c.model or 'nova-2',
                          'encoding': 'linear16', 'sample_rate': 16000, 'channels': 1, 'interim_results': 'true'}
                if c.language:
                    params['language'] = c.language
                url = (c.base_url or 'wss://api.deepgram.com/v1/listen') + '?' + urlencode(params)
                headers = {'Authorization': 'Token ' + c.credentials['api_key']}
            else:
                url = (c.base_url or 'wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1') + '?' + urlencode({'token': await self._aliyun_token()})
            task_id = uuid.uuid4().hex

            def aliyun_command(name: str) -> dict[str, Any]:
                return {'header': {'appkey': c.credentials.get('app_key', ''), 'message_id': uuid.uuid4().hex,
                                   'task_id': task_id, 'namespace': 'SpeechTranscriber', 'name': name}}

            async with self.connector(url, additional_headers=headers, open_timeout=c.timeout,
                                      close_timeout=2, max_size=1024 * 1024, max_queue=8) as ws:
                if c.provider == 'volcengine':
                    body = {'user': {'uid': 'watcherobot'}, 'audio': {'format': 'pcm', 'codec': 'raw', 'rate': 16000, 'bits': 16, 'channel': 1},
                            'request': {**c.options, **c.parameters, 'model_name': c.model or 'bigmodel'}}
                    await ws.send(encode_request(json.dumps(body).encode(), 1))
                    decode_response(await asyncio.wait_for(ws.recv(), c.timeout))
                elif c.provider == 'aliyun':
                    body = aliyun_command('StartTranscription')
                    body['payload'] = {**c.options, **c.parameters, 'format': 'pcm', 'sample_rate': 16000,
                                       'enable_intermediate_result': True}
                    await ws.send(json.dumps(body))
                    initial = json.loads(await asyncio.wait_for(ws.recv(), c.timeout))
                    if initial.get('header', {}).get('name') != 'TranscriptionStarted' or initial['header'].get('status') != 20000000:
                        raise VoiceError('ASR 阿里云无法启动识别；请检查凭据与项目')

                async def send_audio() -> None:
                    sequence = 2
                    async for chunk in audio:
                        if len(chunk) % 2:
                            raise VoiceError('ASR PCM 包含不完整采样')
                        # Limit each wire frame; upload remains naturally backpressured.
                        for offset in range(0, len(chunk), 3200):
                            part = chunk[offset:offset + 3200]
                            await ws.send(encode_request(part, sequence, audio=True) if c.provider == 'volcengine' else part)
                            sequence += 1
                    if c.provider == 'volcengine':
                        await ws.send(encode_request(b'', sequence, audio=True, final=True))
                    elif c.provider == 'deepgram':
                        await ws.send(json.dumps({'type': 'CloseStream'}))
                    else:
                        await ws.send(json.dumps(aliyun_command('StopTranscription')))

                sender = asyncio.create_task(send_audio())
                committed: list[str] = []
                while True:
                    receiver = asyncio.create_task(ws.recv())
                    pending = {receiver, sender} if not sender.done() else {receiver}
                    done, _ = await asyncio.wait(pending, timeout=c.timeout, return_when=asyncio.FIRST_COMPLETED)
                    if not done:
                        raise VoiceError('ASR 等待服务响应超时')
                    if sender.done():
                        sender.result()
                    raw = await asyncio.wait_for(receiver, c.timeout)
                    final = False
                    text = ''
                    if c.provider == 'volcengine':
                        text, final = decode_response(raw)
                    else:
                        data = json.loads(raw)
                        if c.provider == 'deepgram':
                            name = data.get('type')
                            if name == 'Error':
                                raise VoiceError('ASR Deepgram 服务错误')
                            if name == 'Results':
                                text = data['channel']['alternatives'][0]['transcript']
                                if data.get('is_final'):
                                    committed.append(text)
                                text = ''.join(committed) if data.get('is_final') else ''.join(committed) + text
                            final = name == 'Metadata' and sender.done()
                        else:
                            header = data.get('header', {})
                            if header.get('status', 20000000) != 20000000 or header.get('name') == 'TaskFailed':
                                raise VoiceError('ASR 阿里云服务错误')
                            name = header.get('name')
                            text = data.get('payload', {}).get('result', '')
                            if name == 'SentenceEnd':
                                committed.append(text)
                            text = ''.join(committed) if name == 'SentenceEnd' else ''.join(committed) + text
                            final = name == 'TranscriptionCompleted'
                        if final:
                            text = ''.join(committed)
                    if not isinstance(text, str) or len(text) > 20000:
                        raise VoiceError('ASR 响应文本无效或过长')
                    if final:
                        await sender
                        yield Transcript(text.strip())
                        return
                    if text:
                        yield Transcript(text, final=False)
        except VoiceError:
            raise
        except Exception:
            raise VoiceError('ASR 请求失败或响应无效；请检查网络、凭据与资源权限') from None
        finally:
            tasks = [task for task in (sender, receiver) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
