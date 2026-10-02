import asyncio

import httpx
import pytest

from watcherobot.voice.configuration import ModelConfig
from watcherobot.voice.catalog import discover


def test_deepgram_catalog_is_public_and_keeps_unknown_models():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'stt': [{'canonical_name': 'future-asr', 'name': 'Future'}], 'tts': []}))) as client:
            result = await discover('asr', ModelConfig('deepgram'), client=client)
            assert result.supported and result.scope == 'public'
            assert result.items[0].id == 'future-asr'
    asyncio.run(run())


def test_ark_catalog_does_not_request_management_with_inference_key():
    async def run():
        def handler(request):
            pytest.fail('No management request allowed')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover('llm', ModelConfig('ark'), client=client)
            assert not result.supported and '管理' in result.note
    asyncio.run(run())


def test_elevenlabs_voice_pagination():
    async def run():
        def handler(request):
            second = request.url.params.get('page_token') == 'next'
            return httpx.Response(200, json={'voices': [{'voice_id': 'two' if second else 'one', 'name': 'voice'}], 'has_more': not second, 'next_page_token': None if second else 'next'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover('tts', ModelConfig('elevenlabs'), voices=True, client=client)
            assert [x.id for x in result.items] == ['one', 'two']
            assert result.scope == 'account'
    asyncio.run(run())
