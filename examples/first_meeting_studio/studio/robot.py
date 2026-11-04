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
from watcherobot.errors import WatcheRobotError


class FirmwareCompatibilityError(WatcheRobotError, RuntimeError):
    """Safe, actionable capability mismatch for the local dashboard."""


def rms(pcm: bytes) -> float:
    if not pcm or len(pcm) % 2:
        return 0
    samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
    return math.sqrt(sum(x * x for x in samples) / len(samples))


class SDKRobot:
    def __init__(self, robot, stopped: threading.Event, rtc=None, logger=None):
        self.sdk = robot
        self.logger = logger
        self.stopped = stopped
        self.animation_job = None
        self.microphone_stats = {}
        self.microphone_session = None
        self.tracking_active = False
        self.tracking_resume_error: Exception | None = None
        self._active_calm_preset: str | None = None
        self.rtc = rtc
        self._follow_owned = False
        self._voice = None
        self._invitation_handoff = False
        self._special_follow_preset = False

    async def onboarding_screen(self, stage):
        from .waking import WAKE_POSES
        if stage not in ('scan', 'network', 'failed'):
            raise ValueError('未知配网页面')
        if self.stopped.is_set():
            raise InterruptedError('已停止')
        if not self.sdk.supports('expression.provisioning_screens.v1'):
            raise FirmwareCompatibilityError('请更新 ESP32 固件以显示新版配网页面')
        if not self._follow_owned:
            await asyncio.to_thread(self.sdk.expression_runtime.set_audio_follow, True, emotion=0)
            self._follow_owned = True
        self._active_calm_preset = None
        self._invitation_handoff = False
        params = WAKE_POSES[0].lids(transition_ms=0)
        params.update(preset='standby', accessory='provision_' + stage,
                      accessory_opacity=1.0, custom_accessory_layer='front', auto_blink=False, color='#00FF08')
        await self.eyelids(**params)

    async def onboarding_breathe(self, expanded):
        await self.eyelids(accessory_scale=1.05 if expanded else .95, transition_ms=1400)

    async def clear_wake_inputs(self):
        self.sdk.inputs.clear()

    async def wait_wake_touch(self):
        from .onboarding import is_wake_touch
        try:
            event = await asyncio.to_thread(self.sdk.inputs.wait, .1)
        except TimeoutError:
            return False
        return is_wake_touch(event)

    async def finish_invitation(self):
        from .waking import WAKE_POSES
        if self.stopped.is_set():
            raise InterruptedError('已停止')
        job = await asyncio.to_thread(self.sdk.audio.play, 'wifi_connected')
        # Keep the page visible while the success cue plays; never wait on black.
        await self._wait(job, 8, self.sdk.audio.stop)
        await self.eyelids(accessory_opacity=0.0, transition_ms=1200)
        await self._transition_pause(1.25)
        closed = WAKE_POSES[0].lids(transition_ms=0)
        closed.update(accessory='none', custom_accessory_layer='front', accessory_opacity=1.0, color='#00FF08')
        await self.eyelids(**closed)
        self._invitation_handoff = True

    async def _transition_pause(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.stopped.is_set():
                raise InterruptedError('已停止')
            await asyncio.sleep(min(.05, max(0, deadline - time.monotonic())))

    def _uses_rtc_voice(self):
        return self.rtc is not None and self.sdk.supports('expression.audio_follow.v2')

    async def prefetch(self, name: str) -> None:
        if self.sdk.supports('animation.prefetch.v1'):
            await asyncio.to_thread(self.sdk.animation.prefetch, name)

    async def expression(self, name: str) -> None:
        from .scenes import EXPRESSIONS
        if name == 'sleepy' and self._invitation_handoff:
            # Keep the closed lids and hidden mouth. Restarting neutral here
            # would insert one fully awake frame before the waking timeline.
            self._invitation_handoff = False
            return
        self._invitation_handoff = False
        params = dict(EXPRESSIONS[name])
        if not self.sdk.supports("expression.presets.authored.v1"):
            raise FirmwareCompatibilityError("设备不支持最新代码表情，请更新 ESP32 固件（缺少 expression.presets.authored.v1）")
        preset = params.pop("preset")
        emotions = ('neutral', 'smile', 'happy', 'anger', 'concentration', 'sad', 'speechless')
        if preset in emotions and self._uses_rtc_voice():
            if preset == 'neutral' and self._active_calm_preset == preset:
                return
            self._active_calm_preset = None
            self._follow_owned = True
            await asyncio.to_thread(self.sdk.expression_runtime.set_audio_follow, True, emotion=emotions.index(preset))
            if self._special_follow_preset:
                await self.eyelids(preset='standby')
                self._special_follow_preset = False
            if preset == 'neutral':
                self._active_calm_preset = preset
            return
        if self._follow_owned and self._uses_rtc_voice():
            self._active_calm_preset = None
            await self.eyelids(preset=preset, **params)
            self._special_follow_preset = True
            return
        if preset in {"neutral", "smile", "happy", "anger", "concentration", "sad", "speechless"} and not self.sdk.supports("expression.chat.audio.v1"):
            raise FirmwareCompatibilityError("设备固件缺少 expression.chat.audio.v1，无法执行静默表情及语音嘴型；请更新配套 ESP32 固件")
        # Only neutral is timeless. Emotional presets have a firmware deadline;
        # replaying them must remain meaningful. Cache only successful starts.
        if preset == 'neutral' and self._active_calm_preset == preset:
            return
        self._active_calm_preset = None
        await asyncio.to_thread(self.sdk.expression_runtime.start, preset, **params)
        self._follow_owned = False
        if preset == 'neutral':
            self._active_calm_preset = preset

    async def eyelids(self, **params) -> None:
        if self.stopped.is_set():
            raise InterruptedError('已停止')
        await asyncio.to_thread(self.sdk.expression_runtime.update, **params)

    async def finish_expression(self) -> None:
        if self.animation_job:
            await self._wait(self.animation_job, 5, self.sdk.animation.stop)

    async def move(self, pan: int, tilt: int, duration: int) -> None:
        job = await asyncio.to_thread(self.sdk.motion.move_to, pan_deg=pan,
                                      tilt_deg=tilt, duration_ms=duration, profile='ease_in_out')
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
        if self._uses_rtc_voice():
            from .rtc_voice import RtcVoice
            self._voice = RtcVoice(self.rtc, self.stopped, self.logger)
            await self._voice.play(pcm)
            self._voice = None
            return
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
        from watcherobot.errors import WatcheRobotError
        if not self.sdk.supports("camera.capture.seamless_feedback.v1"):
            raise WatcheRobotError("请更新 ESP32：缺少拍照表情无缝交接接口")
        if self._follow_owned and not self.sdk.supports('camera.capture.retained_follow.v1'):
            raise FirmwareCompatibilityError('请更新 ESP32：缺少拍照与语音表情连续交接接口')
        capture_completed = False
        resume_tracking = self.tracking_active
        self.tracking_resume_error = None
        # One camera owner at a time. A failed stop must not launch capture.
        await self.stop_tracking()
        try:
            if not self.sdk.supports("camera.capture.authored_feedback.v1"):
                raise RuntimeError("请更新 ESP32：缺少代码表情同步拍照接口")
            self._active_calm_preset = None
            # Firmware retains the current expression until the camera is ready.
            if self.stopped.is_set():
                raise InterruptedError('已停止')
            image = await asyncio.to_thread(self.sdk.camera.capture_with_feedback, timeout=20)
            capture_completed = True
            if self.stopped.is_set():
                raise InterruptedError('已停止')
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(image.save, path)
        finally:
            self._active_calm_preset = None
            # The firmware owns the photo exit and preserves the RTC canvas.
            # An application stop still performs the full explicit teardown.
            if capture_completed and not self._follow_owned:
                await asyncio.to_thread(self.sdk.expression_runtime.stop)
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
        microphone = self.sdk.microphone.open_pcm(queue_size=64)
        self.microphone_session = microphone
        try:
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
        finally:
            try:
                microphone.close()
            except TimeoutError:
                # Retry only this session's idempotent release. The SDK accepts
                # an explicit not_found response after a lost close ACK. A
                # second timeout remains an error and keeps the cleanup handle.
                self.microphone_stats['close_retries'] = 1
                microphone.close()
        self.microphone_session = None
        self.microphone_stats['state'] = 'closed'
        if self.stopped.is_set() or interrupt.is_set():
            self.microphone_stats['end_reason'] = 'interrupted'
            return b''
        if not started:
            self.microphone_stats['end_reason'] = 'no_speech'
        return b''.join(chunks) if started else b''

    async def stop(self) -> None:
        self._active_calm_preset = None
        self._special_follow_preset = False
        self._invitation_handoff = False
        failures = []
        if self._voice is not None:
            try:
                await self._voice.close()
                self._voice = None
            except Exception as error:
                failures.append('rtc.stop:' + str(error))
        if self._follow_owned:
            try:
                await asyncio.to_thread(self.sdk.expression_runtime.set_audio_follow, False)
                self._follow_owned = False
            except Exception as error:
                failures.append('audio_follow.stop:' + str(error))
        if self.microphone_session is not None:
            try:
                await asyncio.to_thread(self.microphone_session.close)
                self.microphone_session = None
                self.microphone_stats['state'] = 'closed'
            except Exception as error:
                failures.append('microphone.close:' + type(error).__name__ + ': ' + str(error))
        try:
            await self.stop_tracking()
        except Exception as error:
            failures.append('tracking.stop:' + type(error).__name__ + ': ' + str(error))
        for name, stop in (('motion.stop', self.sdk.motion.stop), ('audio.stop', self.sdk.audio.stop), ('expression.stop', self.sdk.expression_runtime.stop)):
            try:
                try:
                    await asyncio.to_thread(stop)
                except TimeoutError:
                    # These three stop commands are idempotent. Retry a lost
                    # ACK once, serially; a second timeout remains a failure.
                    await asyncio.sleep(0.15)
                    await asyncio.to_thread(stop)
            except Exception as error:
                failures.append(name + ':' + type(error).__name__ + ': ' + str(error))
        if failures:
            raise RuntimeError('设备停止请求未全部确认：' + ', '.join(failures))
