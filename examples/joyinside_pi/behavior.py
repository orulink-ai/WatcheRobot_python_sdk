"""Application-owned expressions and finite authored motions, independent of speech providers.

The seven chat_v1 resources are built into the official firmware. No trajectories
are uploaded, no private commands are used, and no second device connection exists.
Doubao's non-RTC audio.play_pcm mouth synchronization requires firmware advertising
expression.audio_follow.local_pcm.v1 in addition to the audio-follow capabilities.
Legacy RTC-only firmware is reported without local PCM mouth synchronization.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable


@dataclass(frozen=True)
class Action:
    action_id: str
    duration_ms: int


ACTIONS = MappingProxyType({emotion: Action('chat_v1_' + name, duration)
                           for emotion, (name, duration) in enumerate((
                               ('talk', 2540), ('smile', 1420), ('happy', 2660),
                               ('angry', 2960), ('focus', 2360), ('sad', 2820),
                               ('resigned', 3460)))})


class ExpressionPolicy:
    """Local Chinese semantics; explicit requests override reply sentiment for four seconds."""

    HOLD_SECONDS = 4.0
    EMOTIONS = ((6, '无语|无奈'), (5, '伤心|难过|失落|遗憾|抱歉'), (3, '生气|愤怒|气死|讨厌'),
                (2, '开心|高兴|兴奋|太棒|太好了'), (4, '认真|仔细|注意|专注|思考'),
                (1, '微笑|友好|谢谢|感谢|欢迎|你好|您好'))

    def __init__(self) -> None:
        self.requested: int | None = None
        self.until = 0.0

    @classmethod
    def infer(cls, text: str) -> int:
        text = re.sub(r'不(?:是很|太|再|怎么)?(?:开心|高兴)', '难过', text)
        # Match only adjacent negation + bounded modifiers + one emotion.
        # Never discard a whole clause: later explicit sentiment remains valid.
        text = re.sub(
            r'(?:不(?:会|要|必|用|是|可能|需要)?|没有|没|别|无需|无须)'
            r'(?:(?:太|再|继续|感到|觉得|变得|那么|这么|很|真的|特别)){0,3}'
            r'(?:生气|愤怒|难过|伤心|担心|失落|遗憾|开心|高兴|兴奋|无语|无奈|微笑)',
            '', text)
        return next((emotion for emotion, pattern in cls.EMOTIONS if re.search(pattern, text)), 0)

    def user_cue(self, text: str, now: float) -> None:
        self.requested = None
        for clause in re.split(r'[，,。；;！!？?\n]', text[:2000]):
            if re.search(r'不要|别|不用|不需要|不必|不是|不想', clause):
                continue
            if re.search(r'恢复(?:正常|中性)|回到(?:正常|中性)|取消.*表情', clause):
                self.requested = 0
            elif re.search(r'表演|展示|表现|做.*表情|来.*表情|给.*表情|请.*(?:一点|表情)', clause):
                self.requested = next((emotion for emotion, pattern in self.EMOTIONS
                                       if re.search(pattern, clause)), None)
        self.until = now + self.HOLD_SECONDS

    def active(self, now: float) -> bool:
        return self.requested is not None and now < self.until

    def resolve(self, now: float, inferred: int) -> int:
        return self.requested if self.active(now) else inferred


class RobotBehavior:
    """One owner with independent eye/action workers and retryable cleanup.

    set_phase submits only the latest state. Complete local actions are never
    queued; their Job terminal state and authored duration + 120 ms must both
    finish before another starts. Command timing is not physical position feedback.
    """

    def __init__(self, robot: Any, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.robot, self.clock = robot, clock
        self.policy = ExpressionPolicy()
        self.active = False
        self.phase = 'paused'
        self.text = ''
        self.device_id: str | None = None
        self.expression_available = self.motion_available = False
        self._expression_owned = self._motion_owned = False
        self._workers: list[asyncio.Task[None]] = []
        self._motion_worker: asyncio.Task[None] | None = None
        self._pending_expression: int | None = None
        self._lock = asyncio.Lock()
        self._errors: dict[str, str] = {}
        self._emotion_submitted: int | None = None
        self._motion_action: str | None = None
        self._commands = 0
        self._missing: list[str] = []

    @property
    def cleanup_pending(self) -> bool:
        return self._expression_owned or self._motion_owned

    def snapshot(self) -> dict[str, Any]:
        return {'active': self.active, 'phase': self.phase, 'device_id': self.device_id,
                'emotion': self._target(), 'emotion_submitted': self._emotion_submitted,
                'expression_available': self.expression_available, 'motion_available': self.motion_available,
                'degraded': bool(self._missing or self._errors), 'missing_capabilities': list(self._missing),
                'error': '; '.join(self._errors.values()), 'cleanup_pending': self.cleanup_pending,
                'motion_action': self._motion_action, 'motion_commands': self._commands,
                'motion_feedback': 'commanded',
                'mouth_sync_available': (self.expression_available
                                         and 'expression.audio_follow.local_pcm.v1'
                                         in (getattr(self.robot, 'capabilities', ()) or ()))}

    def _check_identity(self) -> None:
        if not self.device_id or getattr(self.robot, 'device_info', {}).get('device_id') != self.device_id:
            raise RuntimeError('请重新连接原机器人后重试动作表情清理')

    async def _call(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        # Check again inside the worker, immediately before a hardware mutation.
        def checked() -> Any:
            self._check_identity()
            return method(*args, **kwargs)
        task = asyncio.create_task(asyncio.to_thread(checked))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def start(self) -> None:
        async with self._lock:
            if self.active:
                return
            if self.cleanup_pending:
                raise RuntimeError('上一轮动作表情清理尚未确认，请先重试停止')
            self.device_id = getattr(self.robot, 'device_info', {}).get('device_id')
            self._errors = {}
            self.policy = ExpressionPolicy()
            self._pending_expression = None
            self._commands = 0
            self._emotion_submitted = None
            self.phase, self.text = 'connecting', ''
            capabilities = set(getattr(self.robot, 'capabilities', ()) or ())
            expression_caps = {'expression.audio_follow.v1', 'expression.audio_follow.v2'}
            motion_caps = {'motion', 'motion.action.once.v1'}
            self._missing = sorted((expression_caps | motion_caps) - capabilities)
            self.expression_available = expression_caps <= capabilities
            self.motion_available = motion_caps <= capabilities
            if not self.device_id:
                self._errors['identity'] = '机器人身份不可用，动作表情已停用'
                self.expression_available = self.motion_available = False
                return
            if self.expression_available:
                try:
                    inspect.signature(self.robot.expression_runtime.set_audio_follow).bind(True, emotion=0)
                except (AttributeError, TypeError, ValueError):
                    self.expression_available = False
                    self._errors['sdk'] = '当前 SDK 不支持情绪表情参数'
            self.active = True
            try:
                if self.expression_available:
                    # A lost ACK may still mean the display was acquired.
                    self._expression_owned = True
                    try:
                        await self._call(self.robot.expression_runtime.set_audio_follow, True, emotion=0)
                        self._emotion_submitted = 0
                    except Exception:
                        self.expression_available = False
                        self._errors['expression'] = '表情启动未确认，语音仍可继续'
                if self.expression_available:
                    self._workers.append(asyncio.create_task(self._drive_expression()))
                if self.motion_available:
                    self._start_motion_worker()
            except BaseException:
                self.active = False
                await self._release()
                raise

    def user_cue(self, text: str) -> None:
        self.policy.user_cue(text, self.clock())
        # Pi/tool latency must not consume the user's expression request before
        # any reply is spoken. A new user turn, including negation, replaces it.
        self._pending_expression = self.policy.requested

    async def set_phase(self, phase: str, text: str = '') -> None:
        if phase not in {'connecting', 'listening', 'thinking', 'speaking', 'paused', 'error', 'stopping'}:
            raise ValueError('无效的对话阶段')
        if not isinstance(text, str):
            raise TypeError('text must be a string')
        async with self._lock:
            if phase == 'speaking' and self.phase != 'speaking' and self._pending_expression is not None:
                self.policy.requested = self._pending_expression
                self.policy.until = self.clock() + self.policy.HOLD_SECONDS
                self._pending_expression = None
            if phase == 'listening':
                self.policy.requested = None
                self._pending_expression = None
            self.phase, self.text = phase, text[:2000]
            if self.active and phase in {'listening', 'paused', 'stopping', 'error'}:
                await self._interrupt_motion()
            elif self.active and self.motion_available:
                self._start_motion_worker()

    def _start_motion_worker(self) -> None:
        if self._motion_worker is None or self._motion_worker.done():
            if self._motion_worker in self._workers:
                self._workers.remove(self._motion_worker)
            self._motion_worker = asyncio.create_task(self._drive_motion())
            self._workers.append(self._motion_worker)

    async def _interrupt_motion(self) -> None:
        # Do not allow a late synchronous play ACK to overtake the stop.
        worker, self._motion_worker = self._motion_worker, None
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            if worker in self._workers:
                self._workers.remove(worker)
        if self._motion_owned:
            try:
                await self._call(self.robot.motion.stop)
                self._motion_owned = False
                self._motion_action = None
            except Exception:
                self.motion_available = False
                self._errors['cleanup'] = '动作停止尚未确认，请重新连接原机器人后重试'
                raise RuntimeError(self._errors['cleanup']) from None

    def _target(self) -> int:
        inferred = 4 if self.phase == 'thinking' else self.policy.infer(self.text) if self.phase == 'speaking' else 0
        return self.policy.resolve(self.clock(), inferred)

    async def _drive_expression(self) -> None:
        try:
            while self.active:
                self._check_identity()
                target = self._target()
                if target != self._emotion_submitted:
                    await self._call(self.robot.expression_runtime.set_audio_follow, True, emotion=target)
                    self._emotion_submitted = target
                await asyncio.sleep(.025)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.expression_available = False
            self._errors['expression'] = '表情更新失败，语音仍可继续'

    async def _drive_motion(self) -> None:
        selected: int | None = None
        try:
            while self.active:
                self._check_identity()
                desired = self._target() if (self.phase in {'thinking', 'speaking'}
                                             or self.policy.active(self.clock())) else None
                if desired is None:
                    selected = None
                elif desired != selected:
                    selected = desired
                    action = ACTIONS[desired]
                    self._motion_owned = True
                    job = await self._call(self.robot.motion.play_action, action.action_id)
                    self._motion_action = action.action_id
                    self._commands += 1
                    earliest = self.clock() + action.duration_ms / 1000 + .12
                    while self.active:
                        self._check_identity()
                        if job.state in {'failed', 'cancelled'}:
                            raise RuntimeError('Motion job failed')
                        now = self.clock()
                        if job.state == 'completed' and now >= earliest:
                            break
                        if now >= earliest + 3:
                            raise TimeoutError('Motion completion not confirmed')
                        await asyncio.sleep(.025)
                    self._motion_action = None
                    if desired == 0:
                        next_at = self.clock() + .4
                        while self.active and self.clock() < next_at:
                            await asyncio.sleep(.025)
                        selected = None
                await asyncio.sleep(.025)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.motion_available = False
            self._errors['motion'] = '动作执行未确认，语音仍可继续'
            # A failed/expired job must not leave an uncontrolled local action
            # running until a later voice pause. Retain ownership if stop fails.
            try:
                await self._call(self.robot.motion.stop)
                self._motion_owned = False
                self._motion_action = None
            except Exception:
                self._errors['cleanup'] = '动作停止尚未确认，请重新连接原机器人后重试'

    async def _release(self) -> None:
        self.active = False
        workers, self._workers = self._workers, []
        self._motion_worker = None
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        errors = []
        # Do not issue old-owner cleanup to a newly connected robot.
        if self.cleanup_pending:
            try:
                self._check_identity()
            except RuntimeError:
                self._errors['cleanup'] = '请重新连接原机器人后重试动作表情清理'
                raise
        if self._motion_owned:
            try:
                await self._call(self.robot.motion.stop)
                self._motion_owned = False
                self._motion_action = None
            except Exception as error:
                errors.append(error)
        if self._expression_owned:
            try:
                await self._call(self.robot.expression_runtime.set_audio_follow, False)
                self._expression_owned = False
                self._emotion_submitted = None
            except Exception as error:
                errors.append(error)
        if errors:
            self._errors['cleanup'] = '动作表情清理尚未确认，请重新连接原机器人后重试'
            raise RuntimeError(self._errors['cleanup']) from None
        self._errors.pop('cleanup', None)
        self.phase, self.text = 'paused', ''

    async def stop(self) -> None:
        async with self._lock:
            # Shield teardown itself, not only individual hardware calls. Pause
            # or application shutdown must drain the old owner before returning.
            task = asyncio.create_task(self._release())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.gather(task, return_exceptions=True)
                raise
