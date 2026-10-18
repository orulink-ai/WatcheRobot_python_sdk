"""Sentence playback overlaps synthesis while preserving cancellation ownership."""
import asyncio
import importlib.util
import logging
from pathlib import Path

import pytest

from watcherobot.voice.contracts import AudioChunk, VoiceError

ROOT = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi"


def module(name):
    spec = importlib.util.spec_from_file_location("pi_latency_" + name, ROOT / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
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


def test_next_segment_prefetches_but_third_waits_for_playback():
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
        assert synthesized == ["a", "b"]
        release_first.set()
        await asyncio.wait_for(playing, 1)
        assert device.played == [b"aa", b"bb", b"cc"]

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
