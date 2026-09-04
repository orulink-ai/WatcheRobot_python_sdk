"""One serialized interaction loop owns motion, recording, speech and camera."""
from __future__ import annotations

import asyncio
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

from .config import SECRETS, Settings, save_json
from .dialogue import Dialogue, GREETING, PHOTO_OK
from .gaze import gaze_plan, play_timeline


class MeetingService:
    def __init__(self, robot, cloud, settings: Settings, artifacts: Path, logger=None):
        self.robot = robot
        self.cloud = cloud
        self.settings = settings
        self.artifacts = artifacts
        self.logger = logger
        self.dialogue = Dialogue()
        self.phase = 'ready'
        self.events: deque[dict] = deque(maxlen=300)
        self.history: list[dict] = []
        self.stopped = getattr(robot, 'stopped', threading.Event())
        self.interrupt = threading.Event()
        self.inputs: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        self.task: asyncio.Task | None = None
        self.checks: dict = {}
        self.last_photo = ''

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def log(self, kind: str, text: str) -> None:
        self.events.append({'time': time.strftime('%H:%M:%S'), 'kind': kind, 'text': text})
        if self.logger:
            self.logger.info('%s: %s', kind, text)

    def check_stop(self) -> None:
        if self.stopped.is_set():
            raise InterruptedError('已停止')

    def error_detail(self, error: Exception) -> str:
        from watcherobot.errors import WatcheRobotError
        from .cloud import CloudError
        detail = str(error) if isinstance(error, (CloudError, WatcheRobotError, TimeoutError)) else type(error).__name__
        if type(error) is RuntimeError and str(error) == 'microphone session is closed':
            detail = 'microphone session is closed：设备音频会话提前结束，请重新开始对话'
        detail = detail or type(error).__name__
        for name in SECRETS:
            value = getattr(self.settings, name)
            if value:
                detail = detail.replace(value, '[已隐藏]')
        return detail[:1000]

    def log_exception(self, error: Exception) -> None:
        # Locations only: traceback source lines/locals may contain credentials.
        locations = ' -> '.join(f'{Path(f.filename).name}:{f.lineno}:{f.name}'
                                for f in traceback.extract_tb(error.__traceback__))
        self.log('error', self.error_detail(error) + '；位置：' + locations)

    async def pause(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_stop()
            await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))

    async def cloud_call(self, operation):
        """Cancel HTTP/WebSocket work promptly, never detach hardware threads."""
        task = asyncio.create_task(operation)
        try:
            while not task.done():
                self.check_stop()
                await asyncio.wait({task}, timeout=0.05)
            self.check_stop()
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def say(self, text: str) -> None:
        self.check_stop()
        self.phase = 'synthesizing'
        self.log('robot', text)
        pcm = await self.cloud_call(self.cloud.tts(text))
        self.check_stop()
        self.phase = 'speaking'
        await self.robot.speak(pcm)
        self.history.append({'role': 'assistant', 'content': text})

    async def boot(self) -> None:
        s = self.settings
        self.phase = 'sleeping'
        self.log('stage', '睡觉 → 苏醒 → 抬头 → 左右看 → 打招呼')
        await self.robot.expression(s.sleep_animation)
        await self.robot.move(s.pan_center, s.tilt_down, s.move_ms)
        await self.pause(s.sleep_seconds)
        self.check_stop()
        self.phase = 'waking'
        await self.robot.expression(s.wake_animation)
        await self.robot.move(s.pan_center, s.tilt_up, s.move_ms)
        self.check_stop()
        await self.look_around()
        self.check_stop()
        await self.robot.expression(s.blink_animation)
        await self.say(GREETING)

    async def look_around(self) -> None:
        for direction in ('left', 'right'):
            self.check_stop()
            plan = gaze_plan(self.settings, direction)
            await self.robot.prefetch(plan.animation)
            self.phase = 'looking_' + direction
            self.log('stage', f'{plan.animation}：{plan.duration_ms}ms，按眼神关键帧转头并回中')
            await self.robot.expression(plan.animation)
            async def move(cue):
                await self.robot.move(cue.pan, self.settings.tilt_up, cue.duration_ms)
            await play_timeline(plan, move, self.pause, time.monotonic, self.check_stop,
                                lambda text: self.log('motion', text))
            await self.robot.finish_expression()
            await self.pause(self.settings.look_seconds)

    async def turn(self, text: str) -> None:
        self.check_stop()
        self.log('user', text)
        self.history.append({'role': 'user', 'content': text})
        decision = self.dialogue.next(text)
        if decision.photo:
            self.phase = 'capturing'
            path = self.artifacts / 'photos' / (uuid.uuid4().hex + '.jpg')
            try:
                self.check_stop()
                await self.robot.photo(path)
                self.check_stop()
                save_json(self.artifacts / 'person.json', {
                    'name': self.dialogue.name, 'photo': path.name,
                    'consented_at': time.time(), 'consent_text': text})
            except InterruptedError:
                self.dialogue.stage = 'consent'
                raise
            except Exception as error:
                self.dialogue.stage = 'consent'
                self.log('error', '拍照未成功：' + self.error_detail(error))
                await self.say('刚才没能保存照片。如果愿意再试一次，请说“可以”，也可以不拍照继续聊天。')
                return
            self.last_photo = path.name
            tracking_error = getattr(self.robot, 'tracking_resume_error', None)
            if tracking_error is not None:
                self.log('error', '照片已保存，但恢复端侧跟随失败：' + self.error_detail(tracking_error))
            self.dialogue.stage = 'chat'
            await self.say(PHOTO_OK)
        elif decision.chat:
            self.phase = 'thinking'
            system = {'role': 'system', 'content': (
                '你是刚醒来的桌面机器人 Watcher，好奇、友好。用简短自然的中文回答，通常两三句话，'
                '每次最多问一个问题以继续交流。不能声称执行了动作、拍照或识别人脸；你只负责聊天。'
                '照片只保存在本机，当前没有人脸识别能力。用户名字是：' + self.dialogue.name)}
            await self.say(await self.cloud_call(self.cloud.chat([system] + self.history[-20:])))
        else:
            await self.say(decision.text)
        self.history = self.history[-24:]

    def start(self, boot: bool = True, gaze_only: bool = False) -> None:
        if self.running:
            raise ValueError('应用正在运行，请先停止')
        self.stopped.clear()
        self.interrupt.clear()
        self.dialogue = Dialogue()
        self.history.clear()
        while not self.inputs.empty():
            self.inputs.get_nowait()
        self.task = asyncio.create_task(self._run(boot, gaze_only), name='first-meeting-loop')

    def submit(self, text: str) -> None:
        if not self.running:
            raise ValueError('请先开始开机流程或直接开始对话')
        if self.phase not in ('listening', 'waiting_text') or self.inputs.full():
            raise ValueError('机器人正在执行上一轮，请等它开始聆听')
        self.inputs.put_nowait(text)
        self.interrupt.set()

    def request_stop(self) -> None:
        self.stopped.set()
        self.interrupt.set()
        if self.running:
            self.phase = 'stopping'

    async def _run(self, boot: bool, gaze_only: bool = False) -> None:
        try:
            if gaze_only:
                await self.robot.move(self.settings.pan_center, self.settings.tilt_up, self.settings.move_ms)
                await self.look_around()
                self.log('stage', '左右看测试完成')
                return
            if boot:
                await self.boot()
            else:
                self.log('stage', '直接进入对话，请告诉我“我叫……”')
            if self.settings.face_tracking_enabled:
                self.check_stop()
                await self.robot.start_tracking()
                self.log('stage', '端侧人脸跟随已开启；图像不传到电脑，拍照时短暂停止并恢复')
            while not self.stopped.is_set():
                self.interrupt.clear()
                if not self.inputs.empty():
                    text = self.inputs.get_nowait()
                elif self.settings.voice_enabled:
                    self.phase = 'listening'
                    pcm = await self.robot.listen(self.settings, self.interrupt)
                    stats = getattr(self.robot, 'microphone_stats', {})
                    self.log('audio', f"麦克风 {stats.get('frames', 0)} 帧 / {stats.get('pcm_bytes', 0)} 字节；丢帧 {stats.get('dropped_frames', 0)}，解码错误 {stats.get('decode_failures', 0)}；峰值 {stats.get('peak_rms', 0)} / 阈值 {self.settings.vad_threshold}；结束 {stats.get('end_reason', 'unknown')}")
                    self.check_stop()
                    if not self.inputs.empty():
                        text = self.inputs.get_nowait()
                    elif pcm:
                        self.phase = 'recognizing'
                        text = await self.cloud_call(self.cloud.stt(pcm))
                        if not text.strip():
                            self.log('audio', '语音识别返回空文本，请再说一次')
                    else:
                        continue
                else:
                    self.phase = 'waiting_text'
                    try:
                        text = await asyncio.wait_for(self.inputs.get(), timeout=0.2)
                    except asyncio.TimeoutError:
                        continue
                if text.strip():
                    await self.turn(text.strip())
        except InterruptedError:
            self.log('stage', '流程已停止')
        except Exception as error:
            self.phase = 'error'
            self.log('audio', '异常时麦克风状态：' + str(getattr(self.robot, 'microphone_stats', {})))
            self.log_exception(error)
        finally:
            try:
                await self.robot.stop()
            except Exception as error:
                self.log('error', '设备停止未确认：' + self.error_detail(error))
            if self.phase != 'error':
                self.phase = 'stopped'
