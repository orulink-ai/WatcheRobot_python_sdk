import asyncio
import gzip
import json
import struct

import pytest

from watcherobot.voice.configuration import ModelConfig
from watcherobot.voice.contracts import VoiceError
from watcherobot.voice.providers.asr import StreamingASR
from watcherobot.voice.providers.volc_protocol import decode_response, encode_request


def packet(text='', final=False):
    payload = gzip.compress(json.dumps({'result': {'text': text}}).encode())
    return bytes([0x11, 0x93 if final else 0x91, 0x11, 0]) + struct.pack('>iI', -1 if final else 1, len(payload)) + payload


def test_volc_binary_contract_and_invalid_frame():
    request = encode_request(b'ab', 2, audio=True, final=True)
    assert request[:4] == bytes([0x11, 0x23, 0x01, 0])
    assert struct.unpack('>i', request[4:8])[0] == -2
    assert gzip.decompress(request[12:]) == b'ab'
    assert decode_response(packet('你好', True)) == ('你好', True)
    for bad in (b'', packet()[:-1], b'\x11\x90\x11\x00'):
        with pytest.raises(VoiceError):
            decode_response(bad)


@pytest.mark.parametrize('provider', ['volcengine', 'deepgram', 'aliyun'])
def test_streaming_asr_finishes_sender_and_returns_one_final_utterance(provider):
    async def run():
        sent = []
        ended = asyncio.Event()
        if provider == 'volcengine':
            replies = [packet(), packet('你好'), packet('你好世界', True)]
        elif provider == 'aliyun':
            replies = [json.dumps({'header': {'name': name, 'status': 20000000}, 'payload': {'result': text}})
                       for name, text in [('TranscriptionStarted', ''), ('SentenceEnd', '你好'), ('SentenceEnd', '世界'), ('TranscriptionCompleted', '')]]
        else:
            replies = [json.dumps({'type': 'Results', 'is_final': True, 'channel': {'alternatives': [{'transcript': '你好世界'}]}}), json.dumps({'type': 'Metadata'})]
        class WS:
            async def send(self, value):
                sent.append(value)
                if (isinstance(value, bytes) and value[0:2] == b'\x11\x23') or (isinstance(value, str) and ('StopTranscription' in value or 'CloseStream' in value)):
                    ended.set()
            async def recv(self):
                if len(replies) == 1:
                    await ended.wait()
                return replies.pop(0)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        async def audio():
            yield b'\x01\x00' * 100
        config = ModelConfig(provider, model='custom', credentials={'api_key': 'key', 'app_id': 'app', 'access_token': 'token', 'app_key': 'app', 'token': 'token'})
        model = StreamingASR(config, connector=lambda *a, **kw: WS())
        try:
            results = [x async for x in model.transcribe(audio())]
            assert [x.text for x in results if x.final] == ['你好世界']
            assert ended.is_set()
        finally:
            await model.close()
    asyncio.run(run())


def test_sender_failure_closes_websocket_without_waiting_for_provider():
    async def run():
        closed = []
        class WS:
            async def send(self, value):
                if isinstance(value, bytes):
                    raise OSError('sensitive transport detail')
            async def recv(self):
                await asyncio.sleep(10)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                closed.append(True)
        async def audio():
            yield b'\x00\x00'
        model = StreamingASR(ModelConfig('deepgram', credentials={'api_key': 'placeholder'}), connector=lambda *a, **kw: WS())
        try:
            async def collect():
                return [x async for x in model.transcribe(audio())]
            with pytest.raises(VoiceError) as error:
                await asyncio.wait_for(collect(), 1)
            assert 'sensitive' not in str(error.value)
            assert closed
        finally:
            await model.close()
    asyncio.run(run())
