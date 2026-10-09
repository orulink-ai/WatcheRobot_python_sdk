"""Small, permission-gated robot tool surface. No Daemon or direct device transport."""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import re
import time
import uuid

from PIL import Image
from async_utils import drain_owned
from motion_policy import MotionEnvelope, POSITIONS, PACES
from watcherobot.errors import JobCancelledError


def tool(name, description, properties, required):
    return dict(type='function', name=name, description=description,
                inputSchema=dict(type='object', properties=properties,
                                 required=required, additionalProperties=False))


TOOL_SPECS = [
    tool('observe_scene', '拍摄真实照片并返回图像。current只拍当前视野，不移动；nearby在中心和固件行程两端分别低速拍照，最后等待回中心确认才成功，不要另行回中心。当前硬件配置30至150度、中心90度，每轮最多3张，不是360度也不保证无盲区。须相机及上传权限，nearby还须运动权限。',
         {'scope': {'type': 'string', 'enum': ['current', 'nearby']},
          'pace': {'type': 'string', 'enum': list(PACES), 'description': 'natural默认自然节奏，gentle更慢更轻柔'}}, ['scope']),
    tool('aim_camera', '控制你的机器人身体转头，只转头，不拍照、不上传、不自动回中心。position与pan_deg必须且只能提供一个。pan_deg为30到150的整数，position为固件行程端点或中心。pan_min是小角度端、pan_max是大角度端，精确左右未标定，不猜测方向。pace默认natural，gentle用于慢慢观察。只须运动权限；完成回执不是实测角度。',
         {'position': {'type': 'string', 'enum': list(POSITIONS)},
          'pan_deg': {'type': 'integer', 'minimum': 30, 'maximum': 150},
          'pace': {'type': 'string', 'enum': list(PACES)}}, []),
    tool('set_lights', '设置常亮低亮度灯光，不支持闪烁。color必须为#RRGGBB，如绿色#00FF00，蓝色#0000FF。brightness为0到0.25，10秒后自动关灯。须灯光权限。',
         {'color': {'type': 'string', 'pattern': '^#[0-9A-Fa-f]{6}$'}, 'brightness': {'type': 'number', 'minimum': 0, 'maximum': .25}}, ['color', 'brightness']),
]
TERMINAL = {'completed', 'failed', 'cancelled'}


class RobotTools:
    def __init__(self, robot, publish, exclusive, *, settle=.4, motion=None):
        self.robot, self.publish, self.exclusive = robot, publish, exclusive
        self.settle = settle
        self.motion = motion or MotionEnvelope()
        self.permissions = dict(camera=False, upload=False, motion=False, lights=False)
        self.tasks: list[dict] = []
        self.evidence: list[dict] = []
        self._photos: dict[str, tuple[float, bytes]] = {}
        self.active: asyncio.Task | None = None
        self.epoch = 0
        self.lit = False
        self.light_timer = None
        self.stopping = False
        self.motion_started = False
        self.faulted = False
        self.camera_status = 'unknown'
        self.photo_budget: dict[str, int] = {}
        self.failed_observation_turns: set[str] = set()
        self.light_generation = 0

    def set_permissions(self, values):
        if not isinstance(values, dict) or set(values) - set(self.permissions) or any(type(v) is not bool for v in values.values()):
            raise ValueError('权限必须是 camera/upload/motion/lights 的布尔值')
        self.permissions.update(values)
        self.publish()

    def clear_photos(self):
        self._photos.clear()
        self.evidence.clear()
        self.publish()

    def expire_photos(self):
        expired = {key for key, (created, _) in self._photos.items() if time.monotonic() - created >= 900}
        for key in expired:
            self._photos.pop(key, None)
        self.evidence[:] = [e for e in self.evidence if e['id'] not in expired]

    def photo(self, photo_id):
        self.expire_photos()
        return self._photos[photo_id][1]

    def _check(self, epoch, permissions):
        if epoch != self.epoch or self.stopping or self.faulted:
            raise asyncio.CancelledError()
        if not all(self.permissions[p] for p in permissions):
            raise PermissionError('缺少权限：' + '、'.join(p for p in permissions if not self.permissions[p]))

    async def _call(self, callback, *args, **kwargs):
        # SDK calls run in threads. Cancellation must drain the bounded call so
        # resource release cannot race a late camera/motion command.
        task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await drain_owned(task)
            raise

    def _update(self, task, status, detail=''):
        task.update(status=status, detail=detail)
        self.publish()

    async def _move(self, angle, epoch, needed, pace='natural'):
        self._check(epoch, needed)
        duration = self.motion.duration_ms(angle, pace)
        self.motion_started = True
        job = await self._call(self.robot.motion.move_to, pan_deg=angle, tilt_deg=self.motion.tilt_deg,
                               duration_ms=duration, profile='ease_in_out')
        self._check(epoch, needed)
        await self._call(job.wait, timeout=duration / 1000 + 5)
        await asyncio.sleep(self.settle)
        self._check(epoch, needed)

    async def _observe(self, task, positions, epoch, needed, pace='natural'):
        contents = []
        turn_id = task['turnId']
        if turn_id:
            used = self.photo_budget.get(turn_id, 0)
            if used + len(positions) > 3:
                raise ValueError('本轮最多拍摄3张，不再重复观察；请发起新请求')
            self.photo_budget[turn_id] = used + len(positions)
            while len(self.photo_budget) > 8:
                self.photo_budget.pop(next(iter(self.photo_budget)))
        async with self.exclusive():
            for position in positions:
                self._check(epoch, needed)
                if position != 'current':
                    angle = self.motion.angle(position)
                    duration = self.motion.duration_ms(angle, pace)
                    task.update(targetPanDeg=angle, pace=pace, durationMs=duration)
                    self._update(task, 'moving', f'转到舵机 {angle}°，{"轻柔" if pace == "gentle" else "自然"}节奏 {duration / 1000:g} 秒')
                    await self._move(angle, epoch, needed, pace)
                self._update(task, 'capturing', f'拍摄 {position}')
                try:
                    frame = await self._call(self.robot.camera.capture, width=640, height=480, timeout=12)
                except Exception as error:
                    self.camera_status = 'unavailable'
                    raise RuntimeError('未取得照片，视觉观察不可用：相机拍摄失败或超时') from error
                self._check(epoch, needed)
                data = bytes(frame.data)
                if len(data) > 1024 * 1024:
                    raise ValueError('照片超过 1 MiB 限制')
                with Image.open(io.BytesIO(data)) as image:
                    if image.format != 'JPEG' or image.width * image.height > 1920 * 1080:
                        raise ValueError('设备没有返回有效的受限 JPEG')
                    image.verify()
                self.camera_status = 'available'
                photo_id = uuid.uuid4().hex
                evidence = dict(id=photo_id, taskId=task['id'], view=position,
                    panDeg=None if position == 'current' else self.motion.angle(position),
                    tiltDeg=None if position == 'current' else self.motion.tilt_deg,
                    capturedAt=datetime.now(timezone.utc).isoformat(), sha256=hashlib.sha256(data).hexdigest(),
                    url=f'/api/photos/{photo_id}', device='当前配对机器人', bytes=len(data))
                self.expire_photos()
                self._photos[photo_id] = (time.monotonic(), data)
                self.evidence.append(evidence)
                while len(self.evidence) > 6:
                    self._photos.pop(self.evidence.pop(0)['id'], None)
                task['photos'].append(photo_id)
                contents.extend([dict(type='inputText', text=json.dumps(evidence, ensure_ascii=False)),
                    dict(type='inputImage', imageUrl='data:image/jpeg;base64,' + base64.b64encode(data).decode())])
                self.publish()
            if positions != ['current']:
                self._update(task, 'moving', '回到中心')
                task.update(targetPanDeg=self.motion.center_deg,
                            durationMs=self.motion.duration_ms(self.motion.center_deg, pace))
                await self._move(self.motion.center_deg, epoch, needed, pace)
                contents.append(dict(type='inputText', text=f'本次拍摄及回中心已确认：舵机中心目标{self.motion.center_deg}度的SDK完成回执已收到，不是编码器实测角度。不要另行调用aim_camera回中心或重复拍照。'))
        self._update(task, 'attaching', '本轮照片已采集，等待向 Codex 附入图像')
        return contents

    async def execute(self, name, arguments, *, call_id='', turn_id=''):
        if self.active or self.stopping or self.faulted:
            return self._result(False, '机器人正在处理或停止一个任务，请稍后重试')
        task = dict(id=uuid.uuid4().hex, tool=name, status='authorizing', detail='', photos=[],
                    callId=call_id, turnId=turn_id)
        self.tasks.append(task)
        self.tasks[:] = self.tasks[-20:]
        self.active = asyncio.current_task()
        epoch = self.epoch
        self.motion_started = False
        try:
            if not isinstance(arguments, dict):
                raise ValueError('工具参数必须是对象')
            if name in {'observe_scene', 'aim_camera'} and turn_id and turn_id in self.failed_observation_turns:
                raise ValueError('本轮观察已失败，不自行重试；请由用户显式发起新请求')
            pace = arguments.get('pace', 'natural')
            if pace not in PACES:
                raise ValueError('不支持的转头节奏，未执行设备命令')
            if name == 'observe_scene' and {'scope'} <= set(arguments) <= {'scope', 'pace'} and arguments['scope'] in {'current', 'nearby'}:
                positions = ['current'] if arguments['scope'] == 'current' else ['center', 'pan_min', 'pan_max']
                needed = ['camera', 'upload'] + (['motion'] if positions != ['current'] else [])
                self._check(epoch, needed)
                contents = await self._observe(task, positions, epoch, needed, pace)
                return dict(success=True, contentItems=contents)
            if name == 'aim_camera' and set(arguments) <= {'position', 'pan_deg', 'pace'}:
                if ('position' in arguments) == ('pan_deg' in arguments):
                    raise ValueError('position和pan_deg必须且只能提供一个')
                angle = self.motion.angle(arguments['position']) if 'position' in arguments else arguments['pan_deg']
                duration = self.motion.duration_ms(angle, pace)
                needed = ['motion']
                self._check(epoch, needed)
                task.update(targetPanDeg=angle, pace=pace, durationMs=duration)
                async with self.exclusive():
                    self._update(task, 'moving', f'转到舵机 {angle}°，{"轻柔" if pace == "gentle" else "自然"}节奏 {duration / 1000:g} 秒')
                    await self._move(angle, epoch, needed, pace)
                self._update(task, 'completed', f'转头目标{angle}°的SDK完成回执已收到，未拍照、未上传；保持目标，不额外移动。实际角度未独立测量。')
                return self._result(True, task['detail'])
            if name == 'set_lights' and set(arguments) == {'color', 'brightness'}:
                color, brightness = arguments['color'], arguments['brightness']
                if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color) or type(brightness) not in {int, float} or not 0 <= brightness <= .25:
                    raise ValueError('仅支持 #RRGGBB 常亮灯光与 0–0.25 亮度')
                self._check(epoch, ['lights'])
                if self.light_timer:
                    self.light_timer.cancel()
                    await asyncio.gather(self.light_timer, return_exceptions=True)
                self.light_generation += 1
                # Timer responsibility starts before the command can light the
                # device, not after ACK or microphone reopening succeeds.
                self.light_timer = asyncio.create_task(self._lights_off_after(self.light_generation))
                async with self.exclusive():
                    self._check(epoch, ['lights'])
                    self.lit = True  # Retain cleanup responsibility even if SDK times out.
                    await self._call(self.robot.lights.set_color, color, brightness=brightness)
                    self._check(epoch, ['lights'])
                self._update(task, 'completed', '设备已确认灯光设置，10 秒后关灯')
                return self._result(True, task['detail'])
            raise ValueError('不支持的工具或参数，未执行任何设备命令')
        except asyncio.CancelledError:
            self._update(task, 'cancelled', '已取消，不会执行后续动作')
            return self._result(False, task['detail'])
        except Exception as error:
            if isinstance(error, JobCancelledError) and (epoch != self.epoch or self.stopping):
                self._update(task, 'cancelled', '用户已停止任务，设备确认操作取消')
                return self._result(False, task['detail'])
            if name in {'observe_scene', 'aim_camera'}:
                if task['status'] == 'capturing':
                    self.camera_status = 'unavailable'
                if turn_id:
                    self.failed_observation_turns.add(turn_id)
                    if len(self.failed_observation_turns) > 8:
                        # Only the active turn can call tools; old entries are no
                        # longer executable at the Codex boundary.
                        self.failed_observation_turns = {turn_id}
            if self.motion_started:
                try:
                    await self._call(self.robot.motion.stop)
                except Exception as stop_error:
                    error = RuntimeError(f'{error}；停止未确认：{stop_error}')
                    self.faulted = True
            if name == 'set_lights' and self.lit:
                try:
                    await self._call(self.robot.lights.set_color, '#000000', brightness=0)
                    self.lit = False
                except Exception as off_error:
                    self.faulted = True
                    error = RuntimeError(f'{error}；关灯未确认：{off_error}')
            self._update(task, 'failed', str(error))
            return self._result(False, str(error))
        finally:
            self.active = None

    @staticmethod
    def _result(success, text):
        return dict(success=success, contentItems=[dict(type='inputText', text=text)])

    def validate_result(self, result):
        """Do not re-upload photos after clear, expiry or permission revocation."""
        self.expire_photos()
        if any(item['type'] == 'inputImage' for item in result.get('contentItems', [])):
            if not self.permissions['upload'] or self.faulted:
                return self._result(False, '图像分析权限已撤销，不发送照片')
            for item in result['contentItems']:
                if item['type'] == 'inputText' and item['text'].startswith('{'):
                    evidence = json.loads(item['text'])
                    if evidence.get('id') not in self._photos:
                        return self._result(False, '照片已清除或过期，不重放旧照片')
        return result

    async def _lights_off_after(self, generation):
        try:
            await asyncio.sleep(10)
            if generation != self.light_generation:
                return
            await self._call(self.robot.lights.set_color, '#000000', brightness=0)
            self.lit = False
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.faulted = True
            self.tasks.append(dict(id=uuid.uuid4().hex, tool='set_lights', status='failed', detail=f'自动关灯失败：{error}', photos=[]))
            self.tasks[:] = self.tasks[-20:]
            self.publish()

    async def cancel(self):
        self.epoch += 1
        self.stopping = True
        active = self.active
        was_lit = self.lit
        if active and active is not asyncio.current_task():
            active.cancel()
        failures = []
        try:
            # Never wait for a camera call or model before issuing physical stop.
            try:
                await asyncio.to_thread(self.robot.motion.stop)
            except Exception as error:
                failures.append(str(error))
            if self.light_timer:
                self.light_timer.cancel()
                await asyncio.gather(self.light_timer, return_exceptions=True)
                self.light_timer = None
            if self.lit:
                try:
                    await asyncio.to_thread(self.robot.lights.set_color, '#000000', brightness=0)
                    self.lit = False
                except Exception as error:
                    failures.append(str(error))
            if active and active is not asyncio.current_task():
                await asyncio.gather(active, return_exceptions=True)
                # A thread can send move/on after the first stop/off. Draining
                # it establishes a sending fence; confirm stop again afterward.
                try:
                    await asyncio.to_thread(self.robot.motion.stop)
                except Exception as error:
                    failures.append(str(error))
                if was_lit or self.lit:
                    try:
                        await asyncio.to_thread(self.robot.lights.set_color, '#000000', brightness=0)
                        self.lit = False
                    except Exception as error:
                        failures.append(str(error))
            for task in self.tasks:
                if task['status'] not in TERMINAL:
                    self._update(task, 'cancelled', '用户已停止任务')
        finally:
            self.stopping = False
            self.faulted = bool(failures)
            self.publish()
        if failures:
            raise RuntimeError('停止未确认：' + '; '.join(failures))
