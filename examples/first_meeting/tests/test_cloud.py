import asyncio
import base64
import gzip
import json
import struct

import httpx
import pytest

from meeting.cloud import CloudError, VolcCloud
from meeting.config import Settings
from meeting.speech_protocol import encode_request, decode_response


def test_asr_sequence_and_final_frame():
    packet = encode_request(b'pcm', 3, audio=True, final=True)
    assert packet[:4] == bytes.fromhex('11230100')
    assert struct.unpack('>i', packet[4:8])[0] == -3
    assert gzip.decompress(packet[12:]) == b'pcm'


def test_asr_final_response_and_truncated_packet():
    payload = gzip.compress(json.dumps({'result': {'text': '你好'}}).encode())
    frame = bytes.fromhex('11931100') + struct.pack('>iI', -3, len(payload)) + payload
    assert decode_response(frame) == ('你好', True)
    with pytest.raises(CloudError):
        decode_response(frame[:-1])


def test_tts_rejects_partial_audio_and_does_not_echo_secret():
    async def run():
        body = json.dumps({'code': 0, 'data': base64.b64encode(b'\x00\x00').decode()})
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
        cloud = VolcCloud(Settings(tts_app_id='id', tts_token='secret'), client)
        with pytest.raises(CloudError, match='完整'):
            await cloud.tts('你好')
        await cloud.close()
    asyncio.run(run())


def test_tts_http_failure_does_not_expose_body():
    with pytest.raises(CloudError) as error:
        VolcCloud.check_http(httpx.Response(403, text='secret=private'), 'TTS')
    assert 'private' not in str(error.value)
