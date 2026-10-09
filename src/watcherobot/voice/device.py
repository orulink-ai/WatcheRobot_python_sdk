"""Only this adapter touches SDK hardware; blocking calls are joined on cancel."""
from __future__ import annotations

import asyncio
import math
import struct
from collections import deque
from typing import AsyncGenerator, AsyncIterator, Callable, TypeVar

from watcherobot.media import MicrophoneSession
from watcherobot.robot import WatcheRobot

from .configuration import ConversationConfig
from .contracts import VoiceError

T = TypeVar('T')


async def blocking(call: Callable[[], T]) -> T:
    """Do not leave a device mutation running after its coroutine is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(call))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


class SDKVoiceDevice:
    def __init__(self, robot: WatcheRobot, config: ConversationConfig) -> None:
        self.robot, self.config = robot, config
        self.microphone: MicrophoneSession | None = None
        self._playing = False

    async def ready(self) -> bool:
        try:
            await blocking(lambda: self.robot.refresh_device_info(timeout=1))
            return True
        except Exception:
            return False

    async def utterance(self) -> AsyncGenerator[bytes, None]:
        cfg = self.config
        pre: deque[bytes] = deque()
        pre_bytes = 0
        started = False
        silence = duration = 0.0
        last_frame = asyncio.get_running_loop().time()
        waiting_since = last_frame
        try:
            # Assignment inside the worker ensures cleanup sees even a late-opened lease.
            def open_microphone() -> None:
                self.microphone = self.robot.microphone.open_pcm(queue_size=32)
            await blocking(open_microphone)
            microphone = self.microphone
            assert microphone is not None
            while True:
                try:
                    frame = await blocking(lambda: microphone.read(timeout=0.2))
                except TimeoutError:
                    if asyncio.get_running_loop().time() - last_frame > 4:
                        raise VoiceError('ASR 设备连续 4 秒未返回麦克风音频')
                    continue
                last_frame = asyncio.get_running_loop().time()
                pcm = frame.data
                if not pcm or len(pcm) % 2:
                    raise VoiceError('ASR 设备 PCM 数据无效')
                if microphone.dropped_frames or microphone.decode_failures:
                    raise VoiceError('ASR 麦克风丢帧或解码失败，请重试')
                samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
                active = math.sqrt(sum(x * x for x in samples) / len(samples)) >= cfg.speech_threshold
                seconds = len(pcm) / 32000
                if not started:
                    if not active and last_frame - waiting_since >= cfg.max_utterance_seconds:
                        return
                    if not active:
                        pre.append(pcm)
                        pre_bytes += len(pcm)
                        while pre and pre_bytes > cfg.pre_roll_ms * 32:
                            pre_bytes -= len(pre.popleft())
                        continue
                    started = True
                    for previous in pre:
                        yield previous
                    pre.clear()
                duration += seconds
                silence = 0 if active else silence + seconds
                yield pcm
                if silence >= cfg.silence_ms / 1000 or duration >= cfg.max_utterance_seconds:
                    return
        finally:
            await self._close_microphone()

    async def _close_microphone(self) -> None:
        microphone = self.microphone
        if microphone is not None:
            # Keep ownership until the close is acknowledged so recovery can retry.
            await blocking(microphone.close)
            self.microphone = None

    async def play(self, pcm: bytes) -> None:
        self._playing = True
        try:
            playback = await blocking(lambda: self.robot.audio.play_pcm(pcm, sample_rate_hz=24000))
            deadline = asyncio.get_running_loop().time() + len(pcm) / 48000 + 20
            while asyncio.get_running_loop().time() < deadline:
                try:
                    await blocking(lambda: playback.wait(0.2))
                    return
                except TimeoutError:
                    pass
            raise VoiceError('playback 设备播放超时')
        finally:
            await blocking(self.robot.audio.stop)
            self._playing = False

    async def stop(self) -> None:
        await self._close_microphone()
        if self._playing:
            await blocking(self.robot.audio.stop)
            self._playing = False
