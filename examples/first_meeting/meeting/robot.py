"""The only hardware adapter: all operations use ApplicationContext.robot."""
from __future__ import annotations

import asyncio
import math
import struct
import threading
import time
from collections import deque
from pathlib import Path

from .config import Settings


def rms(pcm: bytes) -> float:
    if not pcm or len(pcm) % 2:
        return 0
    samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
    return math.sqrt(sum(x * x for x in samples) / len(samples))


class SDKRobot:
    def __init__(self, robot, stopped: threading.Event):
        self.sdk = robot
        self.stopped = stopped
        self.animation_job = None
        self.microphone_stats = {}
        self.microphone_session = None
        self.tracking_active = False
        self.tracking_resume_error: Exception | None = None

    async def prefetch(self, name: str) -> None:
        if self.sdk.supports('animation.prefetch.v1'):
            await asyncio.to_thread(self.sdk.animation.prefetch, name)

    async def expression(self, name: str) -> None:
        available = self.sdk.animation.available_ids
        if available and name not in available:
            raise RuntimeError(f'设备未安装动画 {name}，请在配置中选择已安装资源')
        # Animation API does not execute authored sound/motion tracks.
        self.animation_job = await asyncio.to_thread(self.sdk.animation.play, name)

    async def finish_expression(self) -> None:
        if self.animation_job:
            await self._wait(self.animation_job, 5, self.sdk.animation.stop)

    async def move(self, pan: int, tilt: int, duration: int) -> None:
        job = await asyncio.to_thread(self.sdk.motion.move_to, pan_deg=pan,
                                      tilt_deg=tilt, duration_ms=duration, profile='linear')
        await self._wait(job, duration / 1000 + 8, self.sdk.motion.stop)

    async def _wait(self, job, timeout: float, stop) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stopped.is_set():
                await asyncio.to_thread(stop)
                raise InterruptedError('已停止')
            try:
                await asyncio.to_thread(job.wait, 0.2)
                return
            except TimeoutError:
                pass
        await asyncio.to_thread(stop)
        raise TimeoutError('设备操作超时，已请求停止')

    async def speak(self, pcm: bytes) -> None:
        playback = await asyncio.to_thread(self.sdk.audio.play_pcm, pcm, sample_rate_hz=24000)
        await self._wait(playback, len(pcm) / 48000 + 20, self.sdk.audio.stop)

    async def start_tracking(self) -> None:
        from watcherobot.errors import WatcheRobotError
        if self.stopped.is_set():
            raise InterruptedError('已停止')
        if self.tracking_active:
            return
        status = await asyncio.to_thread(self.sdk.vision.status, timeout=8)
        # A released SSCMA camera occasionally times out during reconnect.
        # Query once more after HAL cleanup; never switch the user's model.
        if getattr(status, 'status_code', 0) == 0x107 or getattr(status, 'health', '') == 'busy':
            await asyncio.sleep(0.2)
            if self.stopped.is_set():
                raise InterruptedError('已停止')
            status = await asyncio.to_thread(self.sdk.vision.status, timeout=8)
        if getattr(status, 'health', 'ready') in ('error', 'busy', 'degraded'):
            raise WatcheRobotError(f'视觉模块尚未就绪：health={status.health}, code={status.status_code}')
        if status.model is None or not status.model.contains_face_class:
            raise WatcheRobotError('端侧跟随需要可用的人脸检测模型，请检查视觉固件和当前模型')
        if self.stopped.is_set():
            raise InterruptedError('已停止')
        await asyncio.to_thread(self.sdk.face_tracking.start, timeout=8)
        self.tracking_active = True

    async def stop_tracking(self) -> None:
        if self.tracking_active:
            await asyncio.to_thread(self.sdk.face_tracking.stop, policy='hold', timeout=8)
            self.tracking_active = False

    async def photo(self, path: Path) -> None:
        resume_tracking = self.tracking_active
        self.tracking_resume_error = None
        # One camera owner at a time. A failed stop must not launch capture.
        await self.stop_tracking()
        try:
            if self.stopped.is_set():
                raise InterruptedError('已停止')
            image = await asyncio.to_thread(self.sdk.camera.capture, timeout=10)
            if self.stopped.is_set():
                raise InterruptedError('已停止')
            await asyncio.to_thread(image.save, path)
        finally:
            if resume_tracking and not self.stopped.is_set():
                try:
                    await self.start_tracking()
                except Exception as error:
                    # A saved photo remains successful even if following cannot
                    # resume. The service reports this separate failure.
                    self.tracking_resume_error = error

    async def listen(self, settings: Settings, interrupt: threading.Event) -> bytes:
        return await asyncio.to_thread(self._record_utterance, settings, interrupt)

    def _record_utterance(self, settings: Settings, interrupt: threading.Event) -> bytes:
        self.microphone_stats = {'frames': 0, 'pcm_bytes': 0, 'peak_rms': 0, 'current_rms': 0,
                                 'decode_failures': 0, 'dropped_frames': 0, 'speech_detected': False,
                                 'state': 'opening', 'end_reason': 'waiting', 'threshold': settings.vad_threshold}
        try:
            return self._read_utterance(settings, interrupt)
        except Exception:
            self.microphone_stats.update(state='error', end_reason='error')
            raise

    def _read_utterance(self, settings: Settings, interrupt: threading.Event) -> bytes:
        pre_roll: deque[bytes] = deque(maxlen=5)
        chunks = []
        started = False
        voiced = silence = duration = 0.0
        deadline = time.monotonic() + settings.max_utterance_seconds + 10
        last_frame = time.monotonic()
        with self.sdk.microphone.open_pcm(queue_size=64) as microphone:
            self.microphone_session = microphone
            self.microphone_stats.update(state='waiting_speech', session_id=getattr(microphone, 'id', None))
            while not self.stopped.is_set() and not interrupt.is_set() and time.monotonic() < deadline:
                try:
                    frame = microphone.read(timeout=0.2)
                except TimeoutError:
                    if time.monotonic() - last_frame > 4:
                        raise RuntimeError('机器人麦克风连续 4 秒没有音频，请检查连接')
                    continue
                last_frame = time.monotonic()
                pcm = frame.data
                seconds = len(pcm) / 32000
                volume = rms(pcm)
                self.microphone_stats['frames'] += 1
                self.microphone_stats['pcm_bytes'] += len(pcm)
                self.microphone_stats['peak_rms'] = max(self.microphone_stats['peak_rms'], round(volume))
                self.microphone_stats.update(current_rms=round(volume), last_frame_at=time.time())
                self.microphone_stats['decode_failures'] = microphone.decode_failures
                self.microphone_stats['dropped_frames'] = microphone.dropped_frames
                active = volume >= settings.vad_threshold
                if not started:
                    pre_roll.append(pcm)
                    voiced = voiced + seconds if active else 0
                    if voiced < 0.12:
                        continue
                    started = True
                    self.microphone_stats.update(speech_detected=True, state='recording')
                    chunks.extend(pre_roll)
                else:
                    chunks.append(pcm)
                duration += seconds
                silence = 0 if active else silence + seconds
                if silence >= settings.silence_seconds or duration >= settings.max_utterance_seconds:
                    self.microphone_stats['end_reason'] = 'silence' if silence >= settings.silence_seconds else 'max_duration'
                    break
            if microphone.dropped_frames or microphone.decode_failures:
                raise RuntimeError('麦克风音频丢帧或解码失败，请重试本句话')
            self.microphone_stats['state'] = 'closing'
        self.microphone_session = None
        self.microphone_stats['state'] = 'closed'
        if self.stopped.is_set() or interrupt.is_set():
            self.microphone_stats['end_reason'] = 'interrupted'
            return b''
        if not started:
            self.microphone_stats['end_reason'] = 'no_speech'
        return b''.join(chunks) if started else b''

    async def stop(self) -> None:
        failures = []
        if self.microphone_session is not None:
            try:
                await asyncio.to_thread(self.microphone_session.close)
                self.microphone_session = None
                self.microphone_stats['state'] = 'closed'
            except Exception as error:
                failures.append('microphone.close:' + type(error).__name__)
        try:
            await self.stop_tracking()
        except Exception as error:
            failures.append(type(error).__name__)
        for stop in (self.sdk.motion.stop, self.sdk.audio.stop, self.sdk.animation.stop):
            try:
                await asyncio.to_thread(stop)
            except Exception as error:
                failures.append(type(error).__name__)
        if failures:
            raise RuntimeError('设备停止请求未全部确认：' + ', '.join(failures))
