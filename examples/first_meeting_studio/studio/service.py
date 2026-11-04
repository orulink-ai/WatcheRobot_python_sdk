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
from .scenes import Scene, DEFAULT_SCENES
from .state import MeetingMemory
from .choreography import poses


class MeetingService:
    def __init__(self, robot, cloud, settings: Settings, artifacts: Path, logger=None):
        self.device_id = ""
        self.active_device_id = ""
        self.rehearsal = False
        self.memory = MeetingMemory(artifacts / "people.json")
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
        self.cleanup_required = False
        self.cleanup_lock = asyncio.Lock()
        from .onboarding import Onboarding
        self.onboarding = Onboarding()
        self.include_onboarding = False

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
        if type(error) is RuntimeError and str(error).startswith('设备停止请求未全部确认：'):
            detail = str(error)
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
        await self.robot.expression("speaking")
        try:
            await self.robot.speak(pcm)
        finally:
            if not self.stopped.is_set():
                await self.robot.expression("neutral")
        self.history.append({'role': 'assistant', 'content': text})

    async def boot(self) -> None:
        for scene in self.settings.scenes or DEFAULT_SCENES:
            await self.perform_scene(scene)

    async def perform_scene(self, scene: Scene):
        self.check_stop()
        self.phase = "waking"
        self.log("stage", scene.title)
        await self.robot.expression(scene.expression)
        if scene.expression == 'sleepy':
            from .waking import awaken
            await awaken(self.robot, scene.pan, self.check_stop, self.pause)
            if scene.text:
                await self.say(scene.text)
            return
        for pan, tilt, duration, pause in poses(scene):
            self.check_stop()
            await self.robot.move(pan, tilt, duration)
            await self.pause(pause)
        self.check_stop()
        if scene.text:
            await self.say(scene.text)
        await self.pause(scene.hold_seconds)

    async def look_around(self) -> None:
        for pan in (105, 78, 90):
            await self.perform_scene(Scene(title="眼神与转头排练", pan=pan))

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
            await self.robot.expression("warm")
            await self.say(PHOTO_OK)
        elif decision.chat:
            self.phase = 'thinking'
            await self.robot.expression("thinking")
            system = {'role': 'system', 'content': (
                '你是刚醒来的桌面机器人 Watcher，好奇、友好。用简短自然的中文回答，通常两三句话，'
                '每次最多问一个问题以继续交流。不能声称执行了动作、拍照或识别人脸；你只负责聊天。'
                '照片只保存在本机，当前没有人脸识别能力。用户名字是：' + self.dialogue.name)}
            await self.say(await self.cloud_call(self.cloud.chat([system] + self.history[-20:])))
        else:
            await self.say(decision.text)
        if self.dialogue.stage == "chat" and not self.memory.get(self.active_device_id):
            self.check_stop()
            self.memory.complete(self.active_device_id, self.dialogue.name, self.last_photo, self.rehearsal)
        self.history = self.history[-24:]

    def start(self, boot: bool = True, gaze_only: bool = False, rehearsal: bool = False, onboarding: bool = False, connection_probe: bool = False) -> None:
        if self.running:
            raise ValueError('应用正在运行，请先停止')
        if self.cleanup_required:
            raise ValueError('设备停止尚未确认，请先再次停止以重试清理')
        if not self.device_id:
            raise ValueError("设备身份未知，请等待设备连接")
        self.rehearsal = rehearsal or gaze_only or connection_probe
        self.active_device_id = self.device_id
        self.stopped.clear()
        self.interrupt.clear()
        self.dialogue = Dialogue()
        remembered = self.memory.get(self.device_id) if not self.rehearsal else None
        if remembered:
            self.dialogue.name = remembered["name"]
            self.dialogue.stage = "chat"
            boot = False
        self.history.clear()
        self.include_onboarding = onboarding and boot and not gaze_only and not connection_probe
        self.onboarding.reset()
        if not self.include_onboarding:
            self.onboarding.stage = 'skipped'
        self.last_photo = ''
        while not self.inputs.empty():
            self.inputs.get_nowait()
        self.task = asyncio.create_task(self._run(boot, gaze_only, connection_probe), name='first-meeting-loop')

    def submit(self, text: str) -> None:
        if not self.running:
            raise ValueError('请先开始开机流程或直接开始对话')
        if self.phase not in ('listening', 'waiting_text') or self.inputs.full():
            raise ValueError('机器人正在执行上一轮，请等它开始聆听')
        self.inputs.put_nowait(text)
        self.interrupt.set()

    async def stop(self) -> None:
        self.stopped.set()
        self.interrupt.set()
        if self.running:
            self.phase = 'stopping'
            await self.task
            return
        await self._cleanup_robot()

    def request_stop(self) -> None:
        self.stopped.set()
        self.interrupt.set()
        if self.running:
            self.phase = 'stopping'

    async def _cleanup_robot(self) -> bool:
        async with self.cleanup_lock:
            try:
                await self.robot.stop()
            except Exception as error:
                self.cleanup_required = True
                self.phase = 'cleanup_error'
                self.log('error', '设备停止未确认：' + self.error_detail(error))
                return False
            self.cleanup_required = False
            if self.phase != 'error':
                self.phase = 'stopped'
            return True

    async def _run(self, boot: bool, gaze_only: bool = False, connection_probe: bool = False) -> None:
        try:
            if connection_probe:
                from .waking import awaken, WAKE_POSES
                self.phase = 'connection_probe'
                self.log('stage', '连接衔接短测：连接中→成功→慢睁眼；无收音和对话')
                await self.robot.onboarding_screen('network')
                await self.robot.move(self.settings.pan_center, WAKE_POSES[0].tilt, 900)
                await self.pause(1.2)
                self.check_stop()
                await self.robot.finish_invitation()
                self.check_stop()
                await self.robot.expression('sleepy')
                await awaken(self.robot, self.settings.pan_center, self.check_stop, self.pause)
                await self.pause(1.5)
                self.log('stage', '连接衔接短测完成')
                return
            if gaze_only:
                await self.robot.move(self.settings.pan_center, self.settings.tilt_up, self.settings.move_ms)
                await self.look_around()
                self.log('stage', '左右看测试完成')
                return
            if boot:
                if self.include_onboarding:
                    self.phase = 'onboarding'
                    await self.onboarding.run(self.robot, self.check_stop, self.pause, self.log)
                await self.boot()
                if self.include_onboarding:
                    self.onboarding.stage = 'complete'
            else:
                self.log('stage', '直接进入日常对话' if self.dialogue.stage == "chat" else '请告诉我“我叫……”')
            if self.settings.face_tracking_enabled:
                self.check_stop()
                await self.robot.start_tracking()
                self.log('stage', '端侧人脸跟随已开启；图像不传到电脑，拍照时短暂停止并恢复')
            while not self.stopped.is_set():
                await self.robot.expression("listening")
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
            if self.include_onboarding and self.onboarding.stage not in ('complete', 'skipped'):
                self.onboarding.stage = 'interrupted'
            self.log('stage', '流程已停止')
        except Exception as error:
            if self.include_onboarding and self.onboarding.stage not in ('complete', 'skipped'):
                self.onboarding.stage = 'error'
            self.phase = 'error'
            self.log('audio', '异常时麦克风状态：' + str(getattr(self.robot, 'microphone_stats', {})))
            self.log_exception(error)
        finally:
            await self._cleanup_robot()
