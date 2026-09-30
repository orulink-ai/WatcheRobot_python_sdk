import asyncio

import pytest

from watcherobot.voice.configuration import ConversationConfig, ModelConfig, VoiceConfiguration
from watcherobot.voice.contracts import AudioChunk, Transcript, VoiceError
from watcherobot.voice.runtime import VoiceSession


class ASR:
    def __init__(self, text='问题'):
        self.text = text
    async def transcribe(self, audio):
        async for _ in audio:
            pass
        yield Transcript(self.text)
    async def close(self):
        pass


class LLM:
    def __init__(self):
        self.calls = []
    async def generate(self, messages):
        self.calls.append(list(messages))
        yield '第一句。'
        yield '第二句。'
    async def close(self):
        pass


class TTS:
    async def synthesize(self, text):
        yield AudioChunk(b'\x01')
        yield AudioChunk(b'\x00')
    async def close(self):
        pass


class Device:
    def __init__(self):
        self.listening = False
        self.played = []
        self.stops = 0
    async def utterance(self):
        self.listening = True
        try:
            yield b'\x00\x01'
        finally:
            self.listening = False
    async def play(self, pcm):
        assert not self.listening
        self.played.append(pcm)
    async def stop(self):
        self.stops += 1


def config(**kw):
    model = ModelConfig('fake')
    return VoiceConfiguration(model, model, model, ConversationConfig(**kw), '系统提示')


def test_empty_transcript_never_calls_llm():
    async def run():
        llm, device = LLM(), Device()
        session = VoiceSession(config(), ASR(' '), llm, TTS(), device)
        await session.turn()
        assert not llm.calls and not device.played and not session.history
    asyncio.run(run())


def test_segments_play_half_duplex_and_history_is_bounded():
    async def run():
        device, llm = Device(), LLM()
        session = VoiceSession(config(history_turns=1), ASR(), llm, TTS(), device)
        await session.turn()
        await session.turn()
        assert len(device.played) == 4
        assert len(session.history) == 2
        assert llm.calls[0][0].content == '系统提示'
        assert len(llm.calls[1]) == 4
    asyncio.run(run())


def test_cancel_tts_discards_late_result_and_does_not_commit_history():
    async def run():
        entered = asyncio.Event()
        class LateTTS(TTS):
            async def synthesize(self, text):
                entered.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    pass
                yield AudioChunk(b'\x00\x00')
        device = Device()
        session = VoiceSession(config(), ASR(), LLM(), LateTTS(), device)
        task = asyncio.create_task(session.turn())
        await entered.wait()
        await session.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not device.played and not session.history
        assert device.stops
    asyncio.run(run())


def test_invalid_or_empty_pcm_never_plays():
    async def run():
        class Invalid(TTS):
            async def synthesize(self, text):
                yield AudioChunk(b'\x00', sample_rate=16000)
        device = Device()
        session = VoiceSession(config(), ASR(), LLM(), Invalid(), device)
        with pytest.raises(VoiceError, match='TTS'):
            await session.turn()
        assert not session.history and not device.played
    asyncio.run(run())


def test_reply_limit_and_zero_history():
    async def run():
        class LongLLM(LLM):
            async def generate(self, messages):
                yield 'a' * 1000
        device = Device()
        session = VoiceSession(config(max_reply_characters=100, history_turns=0), ASR(), LongLLM(), TTS(), device)
        await session.turn()
        assert len(device.played) == 1
        assert session.history == []
    asyncio.run(run())


def test_tts_failure_cancels_blocked_llm_producer():
    async def run():
        closed = []
        class EndlessLLM(LLM):
            async def generate(self, messages):
                try:
                    for _ in range(100):
                        yield '下一句。'
                finally:
                    closed.append(True)
        class FailedTTS(TTS):
            async def synthesize(self, text):
                raise VoiceError('TTS failed')
                yield
        session = VoiceSession(config(), ASR(), EndlessLLM(), FailedTTS(), Device())
        with pytest.raises(VoiceError):
            await asyncio.wait_for(session.turn(), 1)
        assert closed and not session.history
    asyncio.run(run())


def test_managed_app_handles_desktop_mic_commands_and_shutdown(monkeypatch):
    import logging
    from types import SimpleNamespace
    from watcherobot.voice.providers import ProviderRegistry
    from watcherobot.voice.runtime import VoiceApplication
    import watcherobot.voice.device as module

    async def run():
        device = Device()
        async def ready():
            return True
        device.ready = ready
        monkeypatch.setattr(module, 'SDKVoiceDevice', lambda *args: device)
        frames = iter(['{"type":"ctrl.microphone.close"}', '{"type":"unknown"}', '{"type":"ctrl.microphone.open"}'])
        app = SimpleNamespace(robot=None, shutdown_requested=False, logger=logging.getLogger('test.voice'))
        async def receive(timeout):
            try:
                return next(frames)
            except StopIteration:
                app.shutdown_requested = True
                await asyncio.sleep(0)
                return None
        app.desktop = SimpleNamespace(receive=receive)
        registry = ProviderRegistry()
        registry.register_asr('fake', lambda c: ASR())
        registry.register_llm('fake', lambda c: LLM())
        registry.register_tts('fake', lambda c: TTS())
        await VoiceApplication(app, config(), registry).run()
        assert device.stops >= 2
        assert len(asyncio.all_tasks()) == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", [VoiceError("LLM 流提前结束"), RuntimeError("private diagnostic")])
def test_llm_failure_during_playback_reports_llm_stage(failure):
    async def run():
        playing = asyncio.Event()
        playback_closed = asyncio.Event()

        class FailingLLM(LLM):
            async def generate(self, messages):
                yield '第一句。'
                await playing.wait()
                raise failure

        class WaitingDevice(Device):
            async def play(self, pcm):
                playing.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    playback_closed.set()

        session = VoiceSession(config(), ASR(), FailingLLM(), TTS(), WaitingDevice())
        with pytest.raises(VoiceError, match='LLM'):
            await asyncio.wait_for(session.turn(), 1)
        assert session.last_error_stage == 'LLM'
        assert playback_closed.is_set()
        assert not session.history

    asyncio.run(run())


def test_next_segment_is_synthesized_while_first_segment_plays():
    async def run():
        second_ready = asyncio.Event()
        calls = []

        class PrefetchTTS(TTS):
            async def synthesize(self, text):
                calls.append(text)
                if len(calls) == 2:
                    second_ready.set()
                yield AudioChunk(b'\x00\x00')

        class SlowDevice(Device):
            async def play(self, pcm):
                if not self.played:
                    await asyncio.wait_for(second_ready.wait(), .5)
                await super().play(pcm)

        device = SlowDevice()
        session = VoiceSession(config(), ASR(), LLM(), PrefetchTTS(), device)
        await asyncio.wait_for(session.turn(), 2)
        assert len(device.played) == 2
        assert len(session.history) == 2

    asyncio.run(run())


def test_disconnect_clears_history_even_when_listening_is_paused(monkeypatch):
    import logging
    from types import SimpleNamespace
    import watcherobot.voice.device as device_module
    import watcherobot.voice.runtime as runtime_module
    from watcherobot.voice.contracts import Message
    from watcherobot.voice.providers import ProviderRegistry

    async def run():
        app = SimpleNamespace(robot=None, shutdown_requested=False, logger=logging.getLogger('test.voice'))
        sessions = []
        entered = asyncio.Event()

        class RememberingSession(VoiceSession):
            def __init__(self, *args):
                super().__init__(*args)
                sessions.append(self)
            async def turn(self):
                self.history.extend([Message('user', 'previous device'), Message('assistant', 'reply')])
                entered.set()
                await asyncio.Event().wait()

        device = Device()
        probes = 0
        async def ready():
            nonlocal probes
            probes += 1
            if probes == 2:
                app.shutdown_requested = True
                return False
            return True
        device.ready = ready
        calls = 0
        async def receive(timeout):
            nonlocal calls
            calls += 1
            if calls == 2:
                await entered.wait()
                return '{"type":"ctrl.microphone.close"}'
            if calls == 3:
                await asyncio.sleep(2.05)
            return None
        app.desktop = SimpleNamespace(receive=receive)
        monkeypatch.setattr(device_module, 'SDKVoiceDevice', lambda *args: device)
        monkeypatch.setattr(runtime_module, 'VoiceSession', RememberingSession)
        registry = ProviderRegistry()
        registry.register_asr('fake', lambda c: ASR())
        registry.register_llm('fake', lambda c: LLM())
        registry.register_tts('fake', lambda c: TTS())
        await asyncio.wait_for(runtime_module.VoiceApplication(app, config(), registry).run(), 4)
        assert probes == 2
        assert sessions[0].history == []

    asyncio.run(run())
