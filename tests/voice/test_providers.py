import asyncio
import json

import httpx
import pytest

from watcherobot.voice.configuration import ModelConfig
from watcherobot.voice.contracts import Message, VoiceError
from watcherobot.voice.providers import ProviderRegistry
from watcherobot.voice.providers.http import ChatLLM, CloudTTS


def test_chat_stream_custom_model_and_endpoint():
    async def run():
        def handler(request):
            body = json.loads(request.content)
            assert str(request.url) == 'https://private.example/v1/chat/completions'
            assert body['model'] == 'future-model'
            assert body['stream'] is True
            assert body['temperature'] == 0.2
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"你好"}}]}\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = ChatLLM(ModelConfig('openai_chat', model='future-model', base_url='https://private.example/v1', parameters={'temperature': 0.2}), client=client)
            assert [x async for x in model.generate([Message('user', 'hi')])] == ['你好']
    asyncio.run(run())


@pytest.mark.parametrize('provider', ['deepgram', 'cartesia', 'elevenlabs', 'minimaxi', 'volcengine'])
def test_tts_requests_pcm_and_decodes_provider_response(provider):
    async def run():
        def handler(request):
            body = json.loads(request.content)
            if provider == 'volcengine':
                assert body['req_params']['audio_params']['sample_rate'] == 24000
                return httpx.Response(200, text='{"code":0,"data":"AQACAA=="}\n{"code":20000000}\n')
            if provider == 'minimaxi':
                assert body['audio_setting']['format'] == 'pcm'
                return httpx.Response(200, json={'base_resp': {'status_code': 0}, 'data': {'audio': '01000200'}})
            if provider == 'cartesia':
                assert body['output_format'] == {'container': 'raw', 'encoding': 'pcm_s16le', 'sample_rate': 24000}
            if provider == 'deepgram':
                assert request.url.params['container'] == 'none'
            if provider == 'elevenlabs':
                assert request.url.params['output_format'] == 'pcm_24000'
            return httpx.Response(200, content=b'\x01\x00\x02\x00')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = CloudTTS(ModelConfig(provider, model='custom', voice_id='voice', credentials={'api_key': 'secret', 'app_id': 'app', 'access_token': 'token'}), client=client)
            chunks = [x async for x in model.synthesize('你好')]
            assert b''.join(x.data for x in chunks) == b'\x01\x00\x02\x00'
            assert all(x.sample_rate == 24000 and x.encoding == 'pcm_s16le' for x in chunks)
    asyncio.run(run())


def test_http_error_does_not_leak_response_or_credentials():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, text='secret-value'))) as client:
            model = ChatLLM(ModelConfig('qwen', model='test', credentials={'api_key': 'secret-value'}), client=client)
            with pytest.raises(VoiceError, match='LLM.*401') as error:
                _ = [x async for x in model.generate([])]
            assert 'secret-value' not in str(error.value)
    asyncio.run(run())


def test_registry_accepts_custom_factories_and_rejects_unknown_provider():
    registry = ProviderRegistry.builtin()
    marker = object()
    registry.register_llm('custom', lambda config: marker)
    assert registry.create_llm(ModelConfig('custom')) is marker
    with pytest.raises(ValueError, match='missing'):
        registry.create_llm(ModelConfig('missing'))
