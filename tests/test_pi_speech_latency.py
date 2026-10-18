"""Sentence playback overlaps synthesis while preserving cancellation ownership."""
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

from watcherobot.voice.contracts import AudioChunk, VoiceError

ROOT = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi"


def module(name):
    spec = importlib.util.spec_from_file_location("pi_latency_" + name, ROOT / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(loaded)
    finally:
        sys.path.pop(0)
    return loaded


def test_chunks_prefer_sentences_and_preserve_exact_text():
    text = "第一句话。第二句话！\nThird sentence. Keep https://example.com intact?"
    chunks = list(module("agent").speech_chunks(text))
    assert chunks == ["第一句话。", "第二句话！", "\n", "Third sentence.", " Keep https://example.com intact?"]
    assert "".join(chunks) == text


def test_chunks_cap_characters_and_utf8_without_splitting_samples():
    text = "机器人🙂" * 100
    chunks = list(module("agent").speech_chunks(text, limit=100))
    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= 80 and len(chunk.encode("utf-8")) <= 100 for chunk in chunks)
    assert max(map(len, module("agent").speech_chunks("a" * 200))) == 80


class Device:
    def __init__(self):
        self.played = []
        self.stops = 0

    async def play(self, pcm):
        self.played.append(pcm)

    async def stop(self):
        self.stops += 1


class TTS:
    async def synthesize(self, text):
        yield AudioChunk(text.encode("ascii") * 2)

    async def close(self):
        pass


def session(device, tts):
    return module("speech_backends").DoubaoSession(device, None, tts, 1, 1)


def test_ready_segments_keep_generating_and_merge_for_legacy():
    async def run():
        second_ready, first_playing, release_first = asyncio.Event(), asyncio.Event(), asyncio.Event()
        synthesized = []

        class Synth(TTS):
            async def synthesize(self, text):
                synthesized.append(text)
                if text == "b":
                    second_ready.set()
                yield AudioChunk(text.encode() * 2)

        class SlowDevice(Device):
            async def play(self, pcm):
                if pcm == b"aa":
                    first_playing.set()
                    await release_first.wait()
                await super().play(pcm)

        device = SlowDevice()
        speech = session(device, Synth())
        playing = asyncio.create_task(speech.say_segments(iter(["a", "b", "c"])))
        await asyncio.wait_for(first_playing.wait(), 1)
        await asyncio.wait_for(second_ready.wait(), 1)
        await asyncio.sleep(0)
        assert synthesized == ["a", "b", "c"]
        release_first.set()
        await asyncio.wait_for(playing, 1)
        assert device.played == [b"aa", b"bbcc"]

    asyncio.run(run())


def test_prefetch_failure_cancels_and_joins_current_playback():
    async def run():
        playing, ended = asyncio.Event(), asyncio.Event()

        class Synth(TTS):
            async def synthesize(self, text):
                if text == "b":
                    await playing.wait()
                    raise VoiceError("TTS failed")
                yield AudioChunk(b"aa")

        class SlowDevice(Device):
            async def play(self, pcm):
                try:
                    playing.set()
                    await asyncio.sleep(30)
                finally:
                    ended.set()

        device = SlowDevice()
        speech = session(device, Synth())
        with pytest.raises(VoiceError, match="TTS failed"):
            await asyncio.wait_for(speech.say_segments(["a", "b"]), 1)
        assert ended.is_set() and speech._task is None and device.stops

    asyncio.run(run())


def test_playback_failure_cancels_and_joins_prefetch():
    async def run():
        synthesizing, ended = asyncio.Event(), asyncio.Event()

        class Synth(TTS):
            async def synthesize(self, text):
                if text == "b":
                    try:
                        synthesizing.set()
                        await asyncio.sleep(30)
                    finally:
                        ended.set()
                yield AudioChunk(b"aa")

        class FailingDevice(Device):
            async def play(self, pcm):
                await synthesizing.wait()
                raise RuntimeError("private-token")

        speech = session(FailingDevice(), Synth())
        with pytest.raises(VoiceError) as failure:
            await asyncio.wait_for(speech.say_segments(["a", "b"]), 1)
        assert "private-token" not in str(failure.value)
        assert ended.is_set() and speech._task is None

    asyncio.run(run())


def test_cancel_joins_resistant_prefetch_and_never_plays_it():
    async def run():
        synthesizing, playing, ended = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Synth(TTS):
            async def synthesize(self, text):
                if text == "b":
                    try:
                        synthesizing.set()
                        await asyncio.sleep(30)
                    except asyncio.CancelledError:
                        yield AudioChunk(b"bb")
                    finally:
                        ended.set()
                else:
                    yield AudioChunk(b"aa")

        class SlowDevice(Device):
            async def play(self, pcm):
                self.played.append(pcm)
                playing.set()
                await asyncio.sleep(30)

        device = SlowDevice()
        speech = session(device, Synth())
        operation = asyncio.create_task(speech.say_segments(["a", "b"]))
        await asyncio.wait_for(playing.wait(), 1)
        await asyncio.wait_for(synthesizing.wait(), 1)
        await asyncio.wait_for(speech.cancel(), 1)
        results = await asyncio.gather(operation, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert ended.is_set() and device.played == [b"aa"] and speech._task is None

    asyncio.run(run())


def test_timing_logs_contain_stage_fields_without_spoken_text(caplog):
    async def run():
        await session(Device(), TTS()).say_segments(["confidential"])

    with caplog.at_level(logging.INFO):
        asyncio.run(run())
    assert "tts_first_audio" in caplog.text and "tts_complete" in caplog.text
    assert "playback_complete" in caplog.text and "elapsed_ms=" in caplog.text
    assert "confidential" not in caplog.text


def test_invalid_prefetched_audio_never_reaches_playback():
    async def run():
        first_playing = asyncio.Event()

        class Synth(TTS):
            async def synthesize(self, text):
                if text == "b":
                    await first_playing.wait()
                yield AudioChunk(b"aa", sample_rate=16000 if text == "b" else 24000)

        class SlowDevice(Device):
            async def play(self, pcm):
                await super().play(pcm)
                first_playing.set()
                await asyncio.sleep(30)

        device = SlowDevice()
        with pytest.raises(VoiceError, match="24 kHz"):
            await asyncio.wait_for(session(device, Synth()).say_segments(["a", "b"]), 1)
        assert device.played == [b"aa"]

    asyncio.run(run())


def test_pipeline_close_failure_retains_lease_until_retry():
    async def run():
        synthesizing, ended = asyncio.Event(), asyncio.Event()

        class Synth(TTS):
            async def synthesize(self, text):
                try:
                    synthesizing.set()
                    await asyncio.sleep(30)
                    yield AudioChunk(b"aa")
                finally:
                    ended.set()

        class FailingStopDevice(Device):
            fail = True

            async def stop(self):
                if self.fail:
                    raise RuntimeError("private-token")
                await super().stop()

        device = FailingStopDevice()
        speech = session(device, Synth())
        operation = asyncio.create_task(speech.say_segments(["a", "b"]))
        await asyncio.wait_for(synthesizing.wait(), 1)
        with pytest.raises(VoiceError, match="释放"):
            await asyncio.wait_for(speech.close(), 1)
        await asyncio.gather(operation, return_exceptions=True)
        assert ended.is_set() and not speech.closed
        with pytest.raises(VoiceError, match="关闭"):
            await speech.say_segments(["c"])
        device.fail = False
        await speech.close()
        assert speech.closed and device.played == []

    asyncio.run(run())


def test_negotiated_continuous_reply_uses_exactly_one_device_stream():
    async def run():
        class StreamingDevice(Device):
            supports_continuous_playback = True
            def __init__(self): super().__init__(); self.streams=[]
            async def play_stream(self,frames): self.streams.append([pcm async for pcm in frames])
            async def play(self,pcm): raise AssertionError('Must not restart per segment')
        device=StreamingDevice();speech=session(device,TTS())
        await speech.say_segments(['a','b','c'])
        assert device.streams==[[b'aa',b'bb',b'cc']]
    asyncio.run(run())


def test_continuous_reply_cancellation_joins_synthesis_and_stream():
    async def run():
        started,producer_ended,stream_ended=asyncio.Event(),asyncio.Event(),asyncio.Event()
        class StreamingDevice(Device):
            supports_continuous_playback=True
            async def play_stream(self,frames):
                try:
                    async for pcm in frames:
                        started.set();await asyncio.Event().wait()
                finally:stream_ended.set()
        class SlowTTS(TTS):
            async def synthesize(self,text):
                if text=='b':
                    try:await asyncio.Event().wait()
                    finally:producer_ended.set()
                yield AudioChunk(b'aa')
        speech=session(StreamingDevice(),SlowTTS())
        playing=asyncio.create_task(speech.say_segments(['a','b']))
        await asyncio.wait_for(started.wait(),1)
        await speech.cancel();await asyncio.gather(playing,return_exceptions=True)
        assert producer_ended.is_set() and stream_ended.is_set()
    asyncio.run(run())


def test_session_synthesizes_async_text_before_gate_and_retains_single_owner():
    async def run():
        gate_entered, release_gate, prepared = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Synth(TTS):
            async def synthesize(self, text):
                yield AudioChunk(text.encode() * 2)
                if text == 'b':
                    prepared.set()
        async def source():
            yield 'a'
            yield 'b'
        async def gate():
            gate_entered.set()
            await release_gate.wait()
        device = Device()
        speech = session(device, Synth())
        operation = asyncio.create_task(speech.say_segments(source(), before_play=gate))
        try:
            await asyncio.wait_for(gate_entered.wait(), 1)
            await asyncio.wait_for(prepared.wait(), 1)
            assert device.played == []
            with pytest.raises(VoiceError, match='已有'):
                await speech.say_segments(['c'])
            release_gate.set()
            await asyncio.wait_for(operation, 1)
            assert b''.join(device.played) == b'aabb' and speech._task is None
        finally:
            release_gate.set()
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
    asyncio.run(run())


def test_session_cancel_while_gate_waits_closes_async_text_before_returning():
    async def run():
        gate_entered, source_closed = asyncio.Event(), asyncio.Event()
        async def source():
            try:
                yield 'a'
                await asyncio.Event().wait()
            finally:
                source_closed.set()
        async def gate():
            gate_entered.set()
            await asyncio.Event().wait()
        device = Device()
        speech = session(device, TTS())
        operation = asyncio.create_task(speech.say_segments(source(), before_play=gate))
        await asyncio.wait_for(gate_entered.wait(), 1)
        await asyncio.wait_for(speech.cancel(), 1)
        result = await asyncio.gather(operation, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert source_closed.is_set() and device.played == [] and speech._task is None
    asyncio.run(run())
