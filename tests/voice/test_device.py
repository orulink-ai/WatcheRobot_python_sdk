import asyncio
import threading
from types import SimpleNamespace

import pytest

from watcherobot.media import MicrophoneSession
from watcherobot.voice.configuration import ConversationConfig
from watcherobot.voice.device import SDKVoiceDevice


def test_microphone_closed_on_silence_and_no_cloud_audio_before_speech():
    async def run():
        active = b'\x00\x10' * 1600
        silent = b'\x00\x00' * 1600
        frames = iter([silent, active, silent, silent])
        class Mic:
            dropped_frames = decode_failures = 0
            closed = False
            def read(self, timeout):
                return SimpleNamespace(data=next(frames))
            def close(self):
                self.closed = True
        mic = Mic()
        robot = SimpleNamespace(microphone=SimpleNamespace(open_pcm=lambda **kw: mic))
        device = SDKVoiceDevice(robot, ConversationConfig(silence_ms=200, pre_roll_ms=100))
        audio = [chunk async for chunk in device.utterance()]
        assert b''.join(audio) == silent + active + silent + silent
        assert mic.closed and device.microphone is None
    asyncio.run(run())


def test_cancel_during_blocking_open_closes_late_microphone():
    async def run():
        entered, release = threading.Event(), threading.Event()
        mic = SimpleNamespace(close=lambda: closed.append(True))
        closed = []
        def open_mic(**kw):
            entered.set()
            release.wait(2)
            return mic
        device = SDKVoiceDevice(SimpleNamespace(microphone=SimpleNamespace(open_pcm=open_mic)), ConversationConfig())
        async def listen():
            async for _ in device.utterance():
                pass
        task = asyncio.create_task(listen())
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert closed == [True]
    asyncio.run(run())


def test_cancel_during_blocking_play_stops_late_playback():
    async def run():
        entered, release = threading.Event(), threading.Event()
        stopped = []
        def play(*args, **kw):
            entered.set()
            release.wait(2)
            return object()
        device = SDKVoiceDevice(SimpleNamespace(audio=SimpleNamespace(play_pcm=play, stop=lambda: stopped.append(True))), ConversationConfig())
        task = asyncio.create_task(device.play(b'\x00\x00'))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert stopped == [True]
    asyncio.run(run())


def test_microphone_close_can_be_retried_after_transient_failure():
    async def run():
        calls = []

        def close(session_id):
            calls.append(session_id)
            if len(calls) == 1:
                raise TimeoutError("simulated close acknowledgement timeout")

        robot = SimpleNamespace(_close_microphone=close)
        device = SDKVoiceDevice(robot, ConversationConfig())
        device.microphone = MicrophoneSession(robot, 42)
        with pytest.raises(TimeoutError):
            await device._close_microphone()
        await device.stop()
        assert calls == [42, 42], "Recovery must retry the unconfirmed close"

    asyncio.run(run())


def test_speaker_stop_can_be_retried_after_transient_failure():
    async def run():
        calls = []

        def stop():
            calls.append(True)
            if len(calls) == 1:
                raise TimeoutError("simulated stop acknowledgement timeout")

        playback = SimpleNamespace(wait=lambda timeout: None)
        audio = SimpleNamespace(play_pcm=lambda *a, **kw: playback, stop=stop)
        device = SDKVoiceDevice(SimpleNamespace(audio=audio), ConversationConfig())
        with pytest.raises(TimeoutError):
            await device.play(b"\x00\x00")
        await device.stop()
        assert len(calls) == 2, "Recovery must retry the unconfirmed stop"

    asyncio.run(run())
