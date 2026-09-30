import asyncio
import threading
from types import SimpleNamespace

import pytest

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
