"""Robot I/O stays half duplex while Application owns cloud speech providers."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

from watcherobot.voice.contracts import AudioChunk, Transcript, VoiceError

ROOT = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi"


def module():
    spec = importlib.util.spec_from_file_location("pi_speech_backends", ROOT / "speech_backends.py")
    loaded = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(loaded)
    finally:
        sys.path.pop(0)
    return loaded


class Device:
    def __init__(self, frames=(b'\x01\x00',)):
        self.frames, self.listening, self.stops = frames, False, 0
        self.played = []
        self.fail_stop = False

    async def ready(self):
        return True

    async def utterance(self):
        self.listening = True
        try:
            for frame in self.frames:
                yield frame
        finally:
            self.listening = False

    async def play(self, pcm):
        assert not self.listening
        self.played.append(pcm)

    async def stop(self):
        self.stops += 1
        if self.fail_stop:
            raise RuntimeError('private token')


class ASR:
    def __init__(self, results=None):
        self.results = results if results is not None else [Transcript('临时', False), Transcript('最终')]
        self.closed = self.stream_closed = self.calls = 0

    async def transcribe(self, frames):
        self.calls += 1
        try:
            async for _ in frames:
                pass
            for result in self.results:
                yield result
        finally:
            self.stream_closed += 1

    async def close(self):
        self.closed += 1


class TTS:
    def __init__(self, chunks=None):
        self.chunks = chunks if chunks is not None else [AudioChunk(b'\x01'), AudioChunk(b'\x00')]
        self.closed = self.stream_closed = 0

    async def synthesize(self, text):
        try:
            for chunk in self.chunks:
                yield chunk
        finally:
            self.stream_closed += 1

    async def close(self):
        self.closed += 1


def make_backend(tmp_path, *, device=None, asr=None, tts=None):
    path = tmp_path / 'doubao.json'
    path.write_text(json.dumps({'app_id': 'test-app', 'access_token': 'private token'}))
    return module().DoubaoBackend(SimpleNamespace(), credentials_path=path,
                                  device=device or Device(), asr=asr or ASR(), tts=tts or TTS())


def test_joyinside_passes_native_session_through():
    calls = []
    session = object()
    speech = SimpleNamespace(open=lambda: session, close=lambda: calls.append('close'))
    backend = module().JoyInsideBackend(SimpleNamespace(speech=speech))
    assert backend.open() is session
    backend.close()
    assert calls == ['close']
    assert backend.snapshot()['configured'] is True


def test_missing_credentials_are_lazy_and_safe(tmp_path):
    backend = module().DoubaoBackend(SimpleNamespace(), credentials_path=tmp_path / 'missing')
    assert backend.snapshot()['configured'] is False
    with pytest.raises(VoiceError, match='豆包'):
        asyncio.run(backend.open())


@pytest.mark.parametrize('data', ['private token', '[]', '{"app_id":42,"access_token":"private token"}',
                                '{"app_id":"test-app","access_token":""}'])
def test_bad_credentials_do_not_leak_content(tmp_path, data):
    path = tmp_path / 'doubao.json'
    path.write_text(data)
    backend = module().DoubaoBackend(SimpleNamespace(), credentials_path=path)
    assert 'private token' not in str(backend.snapshot())
    with pytest.raises(VoiceError) as error:
        asyncio.run(backend.open())
    assert 'private token' not in str(error.value)


def test_default_providers_use_builtin_protocol_and_safe_metadata(tmp_path, monkeypatch):
    mod = module()
    path = tmp_path / 'doubao.json'
    path.write_text(json.dumps({'app_id': 'test-app', 'access_token': 'private token'}))
    configs = {}
    def asr_factory(config):
        configs['asr'] = config
        return ASR()
    def tts_factory(config, **kwargs):
        configs['tts'] = config
        return TTS()
    monkeypatch.setattr(mod, 'StreamingASR', asr_factory)
    monkeypatch.setattr(mod, 'CloudTTS', tts_factory)
    backend = mod.DoubaoBackend(None, credentials_path=path, device=Device())
    async def run():
        session = await backend.open()
        assert configs['asr'].resource_id == 'volc.bigasr.sauc.duration'
        assert configs['tts'].resource_id == 'seed-tts-2.0'
        assert configs['tts'].voice_id == 'zh_female_vv_uranus_bigtts'
        assert configs['asr'].credentials == {'app_id': 'test-app', 'access_token': 'private token'}
        assert 'private token' not in repr(configs['asr'])
        assert 'private token' not in str(backend.snapshot())
        await backend.shutdown()
    asyncio.run(run())


class Pool:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.close_calls = 0
        self.fail_close = False

    async def aclose(self):
        self.close_calls += 1
        if self.fail_close:
            raise RuntimeError('private token')


def test_tts_pool_reused_across_rounds_but_credentials_reload(tmp_path, monkeypatch):
    created = []

    def create_pool(**kwargs):
        pool = Pool(**kwargs)
        created.append(pool)
        return pool

    monkeypatch.setattr(httpx, 'AsyncClient', create_pool)
    path = tmp_path / 'doubao.json'
    path.write_text(json.dumps({'app_id': 'test-app', 'access_token': 'first-test-token'}))
    backend = module().DoubaoBackend(None, credentials_path=path, device=Device(), asr=ASR())
    assert created == [], 'HTTP connections are created only when a real TTS session opens'

    async def run():
        first = await backend.open()
        pool = first.tts.client
        assert pool.options['limits'].keepalive_expiry >= 60, 'connections survive ordinary spoken turns'
        assert 'headers' not in pool.options and 'auth' not in pool.options
        first_headers = first.tts.request('one')[1]
        await backend.close()
        assert pool.close_calls == 0, 'session cleanup leaves the shared pool available'
        path.write_text(json.dumps({'app_id': 'new-test-app', 'access_token': 'second-test-token',
                                    'voice_id': 'new-test-voice'}))
        second = await backend.open()
        assert first is not second and first.tts is not second.tts
        assert second.tts.client is pool and len(created) == 1
        assert second.tts.request('two')[1]['X-Api-Access-Key'] == 'second-test-token'
        assert first_headers['X-Api-Access-Key'] == 'first-test-token'
        assert second.tts.config.voice_id == 'new-test-voice'
        await backend.shutdown()
        assert pool.close_calls == 1
        await backend.shutdown()
        assert pool.close_calls == 1
        with pytest.raises(VoiceError, match='关闭'):
            await backend.open()

    asyncio.run(run())


def test_injected_tts_never_creates_http_pool(tmp_path, monkeypatch):
    def unexpected_pool(**kwargs):
        raise AssertionError('injected TTS must not create an HTTP pool')

    monkeypatch.setattr(httpx, 'AsyncClient', unexpected_pool)
    backend = make_backend(tmp_path)

    async def run():
        await backend.open()
        await backend.shutdown()

    asyncio.run(run())


def test_shutdown_retains_failed_pool_owner_until_retry(tmp_path, monkeypatch):
    pool = Pool()
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: pool)
    path = tmp_path / 'doubao.json'
    path.write_text(json.dumps({'app_id': 'test-app', 'access_token': 'test-token'}))
    backend = module().DoubaoBackend(None, credentials_path=path, device=Device(), asr=ASR())

    async def run():
        await backend.open()
        pool.fail_close = True
        with pytest.raises(VoiceError) as failure:
            await backend.shutdown()
        assert 'private token' not in str(failure.value)
        assert backend._tts_client is pool and backend._session is None
        with pytest.raises(VoiceError, match='关闭'):
            await backend.open()
        pool.fail_close = False
        await backend.shutdown()
        assert pool.close_calls == 2 and backend._tts_client is None

    asyncio.run(run())


def test_shutdown_does_not_close_pool_before_session_cleanup_confirmed(tmp_path, monkeypatch):
    pool, device = Pool(), Device()
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: pool)
    path = tmp_path / 'doubao.json'
    path.write_text(json.dumps({'app_id': 'test-app', 'access_token': 'test-token'}))
    backend = module().DoubaoBackend(None, credentials_path=path, device=device, asr=ASR())

    async def run():
        lease = await backend.open()
        device.fail_stop = True
        with pytest.raises(VoiceError):
            await backend.shutdown()
        assert backend._session is lease and pool.close_calls == 0
        device.fail_stop = False
        await backend.shutdown()
        assert backend._session is None and pool.close_calls == 1

    asyncio.run(run())


class CountingTTS(TTS):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def synthesize(self, text):
        self.calls.append(text)
        async for chunk in super().synthesize(text):
            yield chunk


def test_acknowledgement_pcm_is_reused_across_sessions_only(tmp_path):
    device, tts = Device(), CountingTTS()
    backend = make_backend(tmp_path, device=device, tts=tts)

    async def run():
        first = await backend.open()
        await first.say('received')
        await first.acknowledge('received')
        await backend.close()
        second = await backend.open()
        await second.acknowledge('received')
        await second.say('received')
        assert tts.calls == ['received', 'received', 'received']
        assert device.played == [b'\x01\x00'] * 4
        await backend.shutdown()
        assert backend._acknowledgement_cache.entry is None

    asyncio.run(run())


@pytest.mark.parametrize('field,value', [('access_token', 'new-test-token'),
                                        ('voice_id', 'new-test-voice'),
                                        ('tts_resource_id', 'new-test-resource')])
def test_acknowledgement_cache_invalidates_changed_configuration(tmp_path, field, value):
    tts = CountingTTS()
    backend = make_backend(tmp_path, tts=tts)

    async def run():
        first = await backend.open()
        await first.acknowledge('received')
        await backend.close()
        path = tmp_path / 'doubao.json'
        data = json.loads(path.read_text())
        data[field] = value
        path.write_text(json.dumps(data))
        second = await backend.open()
        await second.acknowledge('received')
        assert tts.calls == ['received', 'received']
        await backend.shutdown()

    asyncio.run(run())


def test_acknowledgement_cache_holds_only_one_phrase(tmp_path):
    tts = CountingTTS()
    backend = make_backend(tmp_path, tts=tts)

    async def run():
        speech = await backend.open()
        for phrase in ['one', 'two', 'one']:
            await speech.acknowledge(phrase)
        assert tts.calls == ['one', 'two', 'one']
        assert backend._acknowledgement_cache.entry[1] == 'one'
        await backend.shutdown()

    asyncio.run(run())


def test_failed_acknowledgement_playback_is_not_cached(tmp_path):
    class FailingDevice(Device):
        fail = True

        async def play(self, pcm):
            if self.fail:
                raise RuntimeError('private token')
            await super().play(pcm)

    device, tts = FailingDevice(), CountingTTS()
    backend = make_backend(tmp_path, device=device, tts=tts)

    async def run():
        speech = await backend.open()
        with pytest.raises(VoiceError) as failure:
            await speech.acknowledge('received')
        assert 'private token' not in str(failure.value)
        assert backend._acknowledgement_cache.entry is None
        device.fail = False
        await speech.acknowledge('received')
        assert tts.calls == ['received', 'received']
        await backend.shutdown()

    asyncio.run(run())


def test_cancelled_acknowledgement_cannot_cache_or_play_late(tmp_path):
    started = asyncio.Event()

    class ResistantTTS(TTS):
        async def synthesize(self, text):
            try:
                started.set()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                yield AudioChunk(b'\0\0')

    device = Device()
    backend = make_backend(tmp_path, device=device, tts=ResistantTTS())

    async def run():
        speech = await backend.open()
        operation = asyncio.create_task(speech.acknowledge('received'))
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(speech.cancel(), 1)
        result = await asyncio.gather(operation, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert device.played == [] and backend._acknowledgement_cache.entry is None
        await backend.shutdown()

    asyncio.run(run())


def test_cancelled_acknowledgement_playback_does_not_promote_cache(tmp_path):
    started = asyncio.Event()

    class SlowDevice(Device):
        async def play(self, pcm):
            try:
                started.set()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                return

    backend = make_backend(tmp_path, device=SlowDevice())

    async def run():
        speech = await backend.open()
        operation = asyncio.create_task(speech.acknowledge('received'))
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(speech.cancel(), 1)
        result = await asyncio.gather(operation, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert backend._acknowledgement_cache.entry is None
        await backend.shutdown()

    asyncio.run(run())


def test_invalid_acknowledgement_audio_never_caches_or_plays(tmp_path):
    device = Device()
    backend = make_backend(tmp_path, device=device, tts=TTS([AudioChunk(b'\0')]))

    async def run():
        speech = await backend.open()
        with pytest.raises(VoiceError, match='采样'):
            await speech.acknowledge('received')
        assert backend._acknowledgement_cache.entry is None and device.played == []
        await backend.shutdown()

    asyncio.run(run())


def test_acknowledgement_cache_retained_until_shutdown_cleanup_confirmed(tmp_path):
    device, tts = Device(), CountingTTS()
    backend = make_backend(tmp_path, device=device, tts=tts)

    async def run():
        speech = await backend.open()
        await speech.acknowledge('received')
        entry = backend._acknowledgement_cache.entry
        device.fail_stop = True
        with pytest.raises(VoiceError):
            await backend.shutdown()
        assert backend._acknowledgement_cache.entry is entry
        device.fail_stop = False
        await backend.shutdown()
        assert backend._acknowledgement_cache.entry is None

    asyncio.run(run())


def test_recognize_uses_final_only_and_closes_capture_before_play(tmp_path):
    device, asr, tts = Device(), ASR(), TTS()
    async def run():
        session = await make_backend(tmp_path, device=device, asr=asr, tts=tts).open()
        assert await session.recognize() == '最终'
        assert not device.listening and asr.stream_closed == 1
        await session.say('你好')
        assert device.played == [b'\x01\x00'] and tts.stream_closed == 1
        await session.close()
        assert asr.closed == tts.closed == 1
    asyncio.run(run())


def test_silence_does_not_open_cloud_request(tmp_path):
    asr = ASR()
    async def run():
        session = await make_backend(tmp_path, device=Device(()), asr=asr).open()
        with pytest.raises(TimeoutError):
            await session.recognize()
        assert asr.calls == 0
        await session.close()
    asyncio.run(run())


def test_partial_transcript_is_not_used_as_final(tmp_path):
    async def run():
        session = await make_backend(tmp_path, asr=ASR([Transcript('临时', False)])).open()
        with pytest.raises(VoiceError, match='最终'):
            await session.recognize()
        await session.close()
    asyncio.run(run())


@pytest.mark.parametrize('chunks', [[AudioChunk(b'\0\0', sample_rate=16000)],
                                   [AudioChunk(b'\0\0', channels=2)],
                                   [AudioChunk(b'\0\0', encoding='wav')],
                                   [AudioChunk(b'\0')], []])
def test_invalid_cloud_audio_never_plays(tmp_path, chunks):
    device, tts = Device(), TTS(chunks)
    async def run():
        session = await make_backend(tmp_path, device=device, tts=tts).open()
        with pytest.raises(VoiceError):
            await session.say('你好')
        assert not device.played and tts.stream_closed == 1
        await session.close()
    asyncio.run(run())


def test_audio_limit_closes_stream_and_avoids_play(tmp_path):
    mod = module()
    device = Device()
    tts = TTS([AudioChunk(b'\0' * (mod.MAX_AUDIO_BYTES + 2))])
    async def run():
        session = await make_backend(tmp_path, device=device, tts=tts).open()
        with pytest.raises(VoiceError, match='大小'):
            await session.say('你好')
        assert not device.played and tts.stream_closed == 1
        await session.close()
    asyncio.run(run())


def test_timeout_retires_capture_and_provider_stream(tmp_path):
    class HangingASR(ASR):
        async def transcribe(self, frames):
            try:
                async for _ in frames:
                    pass
                await asyncio.sleep(30)
                yield Transcript('late')
            finally:
                self.stream_closed += 1
    device, asr = Device(), HangingASR()
    async def run():
        session = await make_backend(tmp_path, device=device, asr=asr).open()
        session.asr_timeout = .01
        with pytest.raises(VoiceError, match='超时'):
            await session.recognize(timeout=1)
        assert not device.listening and asr.stream_closed == 1
        await session.close()
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["operation", "asr"])
def test_legacy_asyncio_timeout_is_not_masked_as_generic_failure(monkeypatch, phase):
    class LegacyAsyncTimeout(Exception):
        pass
    class LegacyASR(ASR):
        async def transcribe(self, frames):
            raise LegacyAsyncTimeout("legacy timeout")
            yield
    mod = module()
    legacy_asyncio = SimpleNamespace(**vars(asyncio))
    legacy_asyncio.TimeoutError = LegacyAsyncTimeout
    monkeypatch.setattr(mod, "asyncio", legacy_asyncio)
    device = Device()
    session = mod.DoubaoSession(device, LegacyASR(), TTS(), 1, 1)

    async def run():
        async def fail():
            raise LegacyAsyncTimeout("legacy timeout")
        if phase == "operation":
            with pytest.raises(TimeoutError):
                await session._run(fail)
        else:
            with pytest.raises(VoiceError, match="超时"):
                await session.recognize(timeout=1)
        assert session._task is None and not device.listening
        await session.close()
    asyncio.run(run())


def test_cancel_joins_playback_before_stopping_device(tmp_path):
    events = []
    started = asyncio.Event()
    class PlayingDevice(Device):
        async def play(self, pcm):
            try:
                started.set()
                await asyncio.sleep(30)
            finally:
                events.append('play-ended')
        async def stop(self):
            events.append('stop')
    async def run():
        session = await make_backend(tmp_path, device=PlayingDevice()).open()
        playing = asyncio.create_task(session.say('你好'))
        await started.wait()
        await session.cancel()
        result = await asyncio.gather(playing, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert events[0] == 'play-ended' and events[1:] and all(event == 'stop' for event in events[1:])
        await session.close()
    asyncio.run(run())


def test_failed_close_retains_owner_for_retry(tmp_path):
    device, asr, tts = Device(), ASR(), TTS()
    backend = make_backend(tmp_path, device=device, asr=asr, tts=tts)
    async def run():
        session = await backend.open()
        device.fail_stop = True
        with pytest.raises(VoiceError) as error:
            await backend.close()
        assert 'private token' not in str(error.value)
        with pytest.raises(VoiceError, match='释放'):
            await backend.open()
        device.fail_stop = False
        await backend.close()
        assert asr.closed == tts.closed == 1
        await session.close()
    asyncio.run(run())


def test_provider_errors_are_safe_and_capture_is_closed(tmp_path):
    class BrokenASR(ASR):
        async def transcribe(self, frames):
            raise RuntimeError('private token')
            yield Transcript('unreachable')
    device = Device()
    async def run():
        session = await make_backend(tmp_path, device=device, asr=BrokenASR()).open()
        with pytest.raises(VoiceError) as error:
            await session.recognize()
        assert 'private token' not in str(error.value)
        assert not device.listening
        await session.close()
    asyncio.run(run())


def test_cancelled_provider_cannot_start_late_playback(tmp_path):
    started = asyncio.Event()
    class ResistantTTS(TTS):
        async def synthesize(self, text):
            try:
                started.set()
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                yield AudioChunk(b'\0\0')
    device = Device()
    async def run():
        session = await make_backend(tmp_path, device=device, tts=ResistantTTS()).open()
        operation = asyncio.create_task(session.say('hello'))
        await started.wait()
        await session.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        assert device.played == []
        await session.close()
    asyncio.run(run())


def test_failed_provider_close_is_retried_without_losing_owner(tmp_path):
    class FailingCloseASR(ASR):
        async def close(self):
            self.closed += 1
            if self.closed == 1:
                raise RuntimeError('private token')
    asr, tts = FailingCloseASR(), TTS()
    backend = make_backend(tmp_path, asr=asr, tts=tts)
    async def run():
        await backend.open()
        with pytest.raises(VoiceError):
            await backend.close()
        with pytest.raises(VoiceError, match='释放'):
            await backend.open()
        await backend.close()
        assert asr.closed == 2 and tts.closed == 1
    asyncio.run(run())


def test_input_audio_limit_closes_microphone(tmp_path):
    device = Device([b'\0' * 1000000])
    async def run():
        session = await make_backend(tmp_path, device=device).open()
        with pytest.raises(VoiceError, match='大小'):
            await session.recognize()
        assert not device.listening
        await session.close()
    asyncio.run(run())


def test_speech_wait_timeout_rearms_and_closes_capture(tmp_path):
    class SilentDevice(Device):
        async def utterance(self):
            self.listening = True
            try:
                await asyncio.sleep(30)
                yield b'\0\0'
            finally:
                self.listening = False
    device, asr = SilentDevice(), ASR()
    async def run():
        session = await make_backend(tmp_path, device=device, asr=asr).open()
        with pytest.raises(TimeoutError):
            await session.recognize(timeout=.01)
        assert not device.listening and not asr.calls
        await session.close()
    asyncio.run(run())


@pytest.mark.parametrize('pause_stage', ['handshake', 'upload'])
def test_slow_asr_does_not_overflow_the_real_32_frame_microphone(tmp_path, pause_stage):
    from watcherobot.media import MicrophoneSession
    from watcherobot.voice.configuration import ConversationConfig
    from watcherobot.voice.device import SDKVoiceDevice

    async def run():
        cloud_started, fed = asyncio.Event(), asyncio.Event()
        closed = []
        robot = SimpleNamespace(_close_microphone=lambda session_id: closed.append(session_id),
                                refresh_device_info=lambda **kwargs: None)
        microphone = MicrophoneSession(robot, 7, queue_size=32)
        robot.microphone = SimpleNamespace(open_pcm=lambda **kwargs: microphone)
        device = SDKVoiceDevice(robot, ConversationConfig(pre_roll_ms=0))
        active, quiet = b'\x00\x10' * 960, b'\x00\x00' * 960
        expected = [active] * 41 + [quiet] * 14
        microphone._push(expected[0], 0)

        async def feed():
            await cloud_started.wait()
            for sequence, frame in enumerate(expected[1:], 1):
                microphone._push(frame, sequence)
                # A 960-sample PCM16 frame represents 60 ms at 16 kHz.
                # Feed at device cadence so Windows thread scheduling does not
                # turn this cloud-backpressure test into a synthetic input flood.
                await asyncio.sleep(.06)
            fed.set()

        received = bytearray()

        class SlowASR(ASR):
            async def transcribe(self, frames):
                if pause_stage == 'handshake':
                    cloud_started.set()
                    await fed.wait()
                async for frame in frames:
                    received.extend(frame)
                    if pause_stage == 'upload' and not cloud_started.is_set():
                        cloud_started.set()
                        await fed.wait()
                yield Transcript('完整识别')

        session = await make_backend(tmp_path, device=device, asr=SlowASR()).open()
        feeder = asyncio.create_task(feed())
        try:
            assert await asyncio.wait_for(session.recognize(), 8) == '完整识别'
            assert received == b''.join(expected)
            assert microphone.dropped_frames == microphone.decode_failures == 0
            assert closed == [7] and device.microphone is None
        finally:
            feeder.cancel()
            await asyncio.gather(feeder, return_exceptions=True)
            await session.close()

    asyncio.run(run())


@pytest.mark.parametrize('pause_stage', ['capture', 'handshake', 'upload'])
def test_cancel_recognition_joins_the_capture_owner(tmp_path, pause_stage):
    async def run():
        entered, source_closed = asyncio.Event(), asyncio.Event()

        class LiveDevice(Device):
            async def utterance(self):
                self.listening = True
                try:
                    if pause_stage == 'capture':
                        entered.set()
                    else:
                        yield b'\x01\x00'
                    await asyncio.Event().wait()
                finally:
                    self.listening = False
                    source_closed.set()

        class WaitingASR(ASR):
            async def transcribe(self, frames):
                if pause_stage == 'upload':
                    await anext(frames)
                entered.set()
                await asyncio.Event().wait()
                yield Transcript('late')

        device = LiveDevice()
        session = await make_backend(tmp_path, device=device, asr=WaitingASR()).open()
        recognizing = asyncio.create_task(session.recognize())
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(session.cancel(), 1)
        await asyncio.gather(recognizing, return_exceptions=True)
        assert source_closed.is_set() and not device.listening
        await session.close()

    asyncio.run(run())


def test_early_asr_final_cannot_hide_incomplete_capture(tmp_path):
    class LiveDevice(Device):
        async def utterance(self):
            self.listening = True
            try:
                yield b'\x01\x00'
                await asyncio.Event().wait()
            finally:
                self.listening = False

    class EarlyASR(ASR):
        async def transcribe(self, frames):
            yield Transcript('premature')

    async def run():
        device = LiveDevice()
        session = await make_backend(tmp_path, device=device, asr=EarlyASR()).open()
        with pytest.raises(VoiceError, match='完整'):
            await asyncio.wait_for(session.recognize(), 1)
        assert not device.listening
        await session.close()

    asyncio.run(run())


def test_capture_failure_during_asr_handshake_is_not_lost(tmp_path):
    class BrokenDevice(Device):
        async def utterance(self):
            self.listening = True
            try:
                yield b'\x01\x00'
                await asyncio.sleep(0)
                raise VoiceError('ASR 采音失败')
            finally:
                self.listening = False

    class DelayedASR(ASR):
        async def transcribe(self, frames):
            await asyncio.Event().wait()
            async for _ in frames:
                pass
            yield Transcript('错误的成功')

    async def run():
        device = BrokenDevice()
        session = await make_backend(tmp_path, device=device, asr=DelayedASR()).open()
        with pytest.raises(VoiceError, match='采音失败'):
            await asyncio.wait_for(session.recognize(), 1)
        assert not device.listening
        await session.close()

    asyncio.run(run())


def test_asr_handshake_error_joins_live_capture_before_return(tmp_path):
    class LiveDevice(Device):
        async def utterance(self):
            self.listening = True
            try:
                yield b'\x01\x00'
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                self.listening = False

    class FailedASR(ASR):
        async def transcribe(self, frames):
            raise VoiceError('ASR 连接失败')
            yield Transcript('never')

    async def run():
        device = LiveDevice()
        session = await make_backend(tmp_path, device=device, asr=FailedASR()).open()
        with pytest.raises(VoiceError, match='连接失败'):
            await session.recognize()
        assert not device.listening
        await session.close()

    asyncio.run(run())


def test_cancelled_recognition_retains_unconfirmed_microphone_for_retry(tmp_path):
    from watcherobot.media import MicrophoneSession
    from watcherobot.voice.configuration import ConversationConfig
    from watcherobot.voice.device import SDKVoiceDevice

    async def run():
        handshake = asyncio.Event()
        fail_close = True
        close_calls = []

        def close_microphone(session_id):
            close_calls.append(session_id)
            if fail_close:
                raise TimeoutError('close acknowledgement missing')

        robot = SimpleNamespace(_close_microphone=close_microphone,
                                refresh_device_info=lambda **kwargs: None)
        microphone = MicrophoneSession(robot, 9)
        microphone._push(b'\x00\x10' * 960, 0)
        robot.microphone = SimpleNamespace(open_pcm=lambda **kwargs: microphone)
        device = SDKVoiceDevice(robot, ConversationConfig())

        class WaitingASR(ASR):
            async def transcribe(self, frames):
                handshake.set()
                await asyncio.Event().wait()
                yield Transcript('never')

        backend = make_backend(tmp_path, device=device, asr=WaitingASR())
        session = await backend.open()
        recognizing = asyncio.create_task(session.recognize())
        await asyncio.wait_for(handshake.wait(), 1)
        with pytest.raises(VoiceError, match='释放'):
            await asyncio.wait_for(session.cancel(), 1)
        await asyncio.gather(recognizing, return_exceptions=True)
        assert device.microphone is microphone and not session.closed
        fail_close = False
        await backend.close()
        assert device.microphone is None and session.closed
        assert close_calls == [9, 9, 9]

    asyncio.run(run())


@pytest.mark.parametrize('stage', ['open', 'close'])
def test_capture_device_timeout_is_not_reported_as_normal_silence(tmp_path, stage):
    class TimeoutDevice(Device):
        async def utterance(self):
            if stage == 'open':
                raise TimeoutError('private device detail')
            self.listening = True
            try:
                yield b'\x01\x00'
            finally:
                self.listening = False
                raise TimeoutError('private device detail')

    async def run():
        device = TimeoutDevice()
        session = await make_backend(tmp_path, device=device).open()
        with pytest.raises(VoiceError, match='采音.*超时') as failure:
            await session.recognize()
        assert 'private' not in str(failure.value)
        await session.close()

    asyncio.run(run())


def test_playback_timeout_stops_device_and_fails_visibly(tmp_path, monkeypatch):
    mod = module()
    device = Device()
    real_wait = asyncio.wait_for
    async def timed_wait(awaitable, timeout):
        if timeout is not None and 20 <= timeout < 21:
            awaitable.close()
            raise TimeoutError
        return await real_wait(awaitable, timeout)
    monkeypatch.setattr(mod.asyncio, 'wait_for', timed_wait)
    async def run():
        session = await make_backend(tmp_path, device=device).open()
        with pytest.raises(VoiceError, match='播放超时'):
            await session.say('hello')
        assert device.stops == 1
        await session.close()
    asyncio.run(run())
