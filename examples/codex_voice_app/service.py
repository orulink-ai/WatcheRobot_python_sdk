"""Session lifecycle; device access is injected from ApplicationContext only."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import copy
from contextlib import asynccontextmanager
import math
import struct
import uuid

from audio_bridge import OutputBuffer
from body_feedback import BodyFeedback
from async_utils import drain_owned
from codex_agent import CodexAgent
from errors import BusyRequest, error_message
from turn_deadline import TurnDeadline, VOICE_IDLE_SECONDS
from protocol import audio_params
from robot_tools import RobotTools, TERMINAL
from watcherobot.errors import JobCancelledError


class VoiceService:
    def __init__(self, robot, device_online: Callable[[], Awaitable[bool]],
                 agent_factory=CodexAgent, *, output_gap: float = .6,
                 clock=None, watchdog_interval: float = .25) -> None:
        self.robot = robot
        self.device_online = device_online
        self.agent_factory = agent_factory
        self.output_gap = output_gap
        self.clock = clock
        self.watchdog_interval = watchdog_interval
        self.agent = None
        self.mic = None
        self.mic_open_uncertain = False
        self.mic_task = None
        self.tasks: set[asyncio.Task] = set()
        self.device_owners: set[asyncio.Task] = set()
        self.subscribers: set[asyncio.Queue] = set()
        self.lock = asyncio.Lock()
        self.media_lock = asyncio.Lock()
        self.robot_tools = RobotTools(robot, self.publish, self._robot_exclusive)
        self.body_feedback = BodyFeedback(robot)
        self.playback_task = None
        self.cancel_latched = False
        self.stopping = False
        self.answer_done = False
        self.answer_turn = ''
        self.current_turn = ''
        self.turn_watchdog = None
        self.turn_deadline = None
        self.last_task_signature = ()
        self.last_audio_at = 0.0
        self.last_upstream_audio_at = 0.0
        self.answer_received_bytes = self.answer_played_bytes = 0
        self.answer_voice_epoch = None
        self.speech_prepare_task = None
        self.prepared_voice_epoch = None
        self.answer_audio_format = None
        self.lifecycle_task = None
        self.lifecycle_epoch = 0
        self.stop_task = None
        self.cleanup_task = None
        self.physical_stop_task = None
        self.shutdown_failures = []
        self.output = OutputBuffer()
        self.play_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
        self.muted = False
        self.transcript_ids: dict[str, str] = {}
        self.voice_commits: set[tuple] = set()
        self.voice_generation = 0
        self.voice_draft_active = False
        self.voice_has_handoff = False
        self.voice_handoffs: set[tuple] = set()
        self.agent_event_inflight = False
        self.latency_marks = {}
        self.state = dict(connected=False, microphone=False, speaking=False,
                          busy=False, deviceOnline=False, error='', notice='', messages=[],
                          micFrames=0, outputBytes=0, muted=False,
                          agentWorking=False, stopStatus='idle', stopReason='', agentTurns=0)
        self.state['agentEvents'] = []

    def snapshot(self) -> dict:
        self.robot_tools.expire_photos()
        phase, detail = self._interaction()
        return copy.deepcopy({**self.state, 'permissions': self.robot_tools.permissions,
                              'tasks': self.robot_tools.tasks, 'evidence': self.robot_tools.evidence,
                              'cameraStatus': self.robot_tools.camera_status,
                              'motionRange': self.robot_tools.motion.snapshot(),
                              'interactionPhase': phase, 'interactionDetail': detail,
                              'bodyFeedback': self.body_feedback.status,
                              'turnLatency': dict(self.latency_marks),
                              'agentInfo': getattr(self.agent, 'info', {}),
                              'voiceDiagnostics': self.agent.diagnostics() if self.agent and hasattr(self.agent, 'diagnostics')
                                  else self.state.get('voiceDiagnostics', {})})

    def _interaction(self):
        if self.state['busy']:
            return 'connecting', '正在准备会话连接'
        if not self.state['connected']:
            return ('error' if self.state['error'] else 'idle'), ''
        if self.state['speaking']:
            return 'speaking', '设备正在播放回答'
        if self.state['agentWorking']:
            speech = self.state.get('speechStatus')
            if speech == 'settling':
                return 'settling', '等待语音尾段与设备完成确认'
            if speech in {'connecting', 'requested', 'streaming'}:
                return 'connecting', '回答已生成，正在准备语音'
            for task in reversed(self.robot_tools.tasks):
                if task.get('turnId') == self.current_turn and task['status'] in {'moving', 'capturing', 'attaching', 'analyzing'}:
                    return ('analyzing' if task['status'] == 'attaching' else task['status']), task['detail']
            return 'thinking', '已收到请求，Codex正在处理'
        return ('listening' if self.mic and not self.muted else 'idle'), ''

    def _now(self):
        return self.clock() if self.clock else asyncio.get_running_loop().time()

    def _accept_latency(self):
        self.request_started_at = self._now()
        self.latency_marks = dict(requestAccepted=0.0)

    def _mark_latency(self, stage):
        if self.latency_marks:
            self.latency_marks.setdefault(stage, round(self._now() - self.request_started_at, 3))

    async def _watch_turn(self, turn_id, deadline):
        while self.state['connected'] and not self.stopping and self.current_turn == turn_id:
            await asyncio.sleep(self.watchdog_interval)
            if self.turn_deadline is not deadline or self.current_turn != turn_id or self.stopping:
                return
            now = self._now()
            self._finish_answer(now)
            message = deadline.error(now, progress_pending=self._agent_event_pending())
            if message:
                raise RuntimeError(message)
            if deadline.phase == 'done':
                return

    def publish(self) -> None:
        signature = tuple((task['id'], task['status'], len(task['photos'])) for task in self.robot_tools.tasks
                          if task.get('turnId') == self.current_turn)
        if signature != self.last_task_signature:
            if any(task.get('turnId') == self.current_turn and task['status'] in {'moving', 'capturing'}
                   for task in self.robot_tools.tasks):
                self._mark_latency('firstTool')  # Tool intent, not proof of physical motion.
            if self.turn_deadline and signature:
                self.turn_deadline.task_progress(self._now())
            self.last_task_signature = signature
        if self.robot_tools.faulted and self.state['connected'] and not self.stopping:
            self.state['error'] = '设备动作或关灯未确认，正在安全结束会话'
            self.stopping = self.cancel_latched = True
            self._spawn(lambda: self.stop(reason='device-action-unconfirmed'))
        self.state['microphone'] = bool(self.mic and not self.muted)
        self.state['muted'] = self.muted
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self.snapshot())

    async def refresh_device(self) -> None:
        try:
            online = await self.device_online()
        except Exception:
            online = False
        if online != self.state['deviceOnline']:
            self.state['deviceOnline'] = online
            self.publish()
        if self.state['connected'] and not online:
            self.state['error'] = '机器人连接已断开'
            await self.stop(reason='device-disconnected')

    def record_failure(self, error):
        self.state['error'] = error_message(error)
        self.state['lastFailure'] = dict(type=type(error).__name__, message=self.state['error'])

    def _spawn(self, factory) -> asyncio.Task:
        task = asyncio.create_task(self._guard(factory))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _guard(self, factory) -> None:
        try:
            await factory()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.record_failure(error)
            for task in self.robot_tools.tasks:
                if task['status'] not in TERMINAL:
                    task.update(status='failed', detail='本轮未完成：' + error_message(error))
            # Do not make a background task wait for cleanup that cancels it.
            if self.stopping:
                self.shutdown_failures.append(error_message(error))
            else:
                asyncio.create_task(self.stop(reason='runtime-error'))

    async def start(self) -> None:
        epoch = self.lifecycle_epoch
        if self.state['stopStatus'] == 'stopping':
            raise asyncio.CancelledError()
        async with self.lock:
            if epoch != self.lifecycle_epoch:
                raise asyncio.CancelledError()
            if self.state['stopStatus'] == 'unconfirmed' or self.robot_tools.faulted:
                raise RuntimeError('上次停止未确认，请先重试停止并检查设备')
            if self.state['connected']:
                return
            self.state.update(busy=True, error='', notice='', micFrames=0, outputBytes=0, stopStatus='idle', stopReason='')
            self.state['speechStatus'] = 'idle'
            self.stopping = self.cancel_latched = False
            self.physical_stop_task = None
            self.cleanup_task = None
            self.shutdown_failures.clear()
            self.lifecycle_task = asyncio.current_task()
            self.answer_done = False
            self.answer_turn = self.current_turn = ''
            self.speech_prepare_task = None
            self.prepared_voice_epoch = None
            self.publish()
            try:
                await self.refresh_device()
                if not self.state['deviceOnline']:
                    raise RuntimeError('机器人未连接，请先通过 SDK Daemon 配对')
                self.agent = self.agent_factory()
                self.agent.tool_handler = self.robot_tools.execute
                self.agent.tool_result_validator = self.robot_tools.validate_result
                await self.agent.start()  # Wait for actual upstream readiness before opening mic.
                if self.stopping or self.cancel_latched:
                    raise asyncio.CancelledError()
                await self._open_microphone()
                self.muted = False
                self.state.update(connected=True, messages=[], busy=False)
                self.mic_task = self._spawn(self._microphone)
                self._spawn(self._events)
                self.playback_task = self._spawn(self._playback)
                self.publish()
            except BaseException as error:
                if not isinstance(error, asyncio.CancelledError):
                    self.record_failure(error)
                if not self.state['stopReason']:
                    self.state['stopReason'] = 'start-failed'
                if self.stop_task is None:
                    await self._cleanup()
                raise
            finally:
                self.lifecycle_task = None

    async def _physical_stop(self):
        stops = [asyncio.to_thread(self.robot.audio.stop)]
        if hasattr(self.robot, 'motion'):
            stops.append(self.robot_tools.cancel())
        if self.mic:
            stops.append(asyncio.to_thread(self.mic.close))
        results = await asyncio.gather(*stops, return_exceptions=True)
        return [error_message(result) for result in results if isinstance(result, BaseException)]

    async def _cleanup(self, owner=None) -> None:
        if self.cleanup_task is None:
            self.cleanup_task = asyncio.create_task(self._cleanup_impl(owner or asyncio.current_task()))
        try:
            await asyncio.shield(self.cleanup_task)
        except asyncio.CancelledError:
            await drain_owned(self.cleanup_task)
            raise

    async def _cleanup_impl(self, owner) -> None:
        self.stopping = True
        self.cancel_latched = True
        self.answer_turn = self.current_turn = ''
        if self.turn_deadline:
            self.turn_deadline.finish()
        if self.agent and hasattr(self.agent, 'invalidate_turn'):
            self.agent.invalidate_turn()
        self.state['stopStatus'] = 'stopping'
        self.publish()
        failures = []
        # Issue both physical stops before draining an in-flight camera call.
        if not self.physical_stop_task:
            self.physical_stop_task = asyncio.create_task(self._physical_stop())
        failures.extend(await self.physical_stop_task)
        if not self.state['deviceOnline']:
            failures.append('机器人离线，设备停止未确认')
        current = owner or asyncio.current_task()
        others = [task for task in self.tasks | self.device_owners if task is not current]
        for task in others:
            task.cancel()
        await asyncio.gather(*others, return_exceptions=True)
        self.tasks.clear()
        self.mic_task = None
        self.speech_prepare_task = None
        self.prepared_voice_epoch = None
        try:
            await self.body_feedback.close()
        except Exception as error:
            failures.append('设备表情释放未确认：' + error_message(error))
        mic, agent = self.mic, self.agent
        for callback in ([mic.close] if mic else []):
            try:
                await asyncio.to_thread(callback)
                self.mic = None
            except Exception as error:
                failures.append(error_message(error))
        if agent:
            if hasattr(agent, 'diagnostics'):
                self.state['voiceDiagnostics'] = agent.diagnostics()
            try:
                await agent.close()
                self.agent = None
            except Exception as error:
                failures.append(error_message(error))
        self.output = OutputBuffer()
        self.play_queue = asyncio.Queue(maxsize=4)
        self.transcript_ids.clear()
        self.voice_commits.clear()
        self.voice_handoffs.clear()
        self.voice_draft_active = False
        self.state.update(connected=False, busy=False, speaking=False, agentWorking=False)
        # A cancelled in-flight unmute can discover its lost ACK only while
        # draining owners. Check uncertainty after the sending fence, not before.
        if self.mic_open_uncertain:
            failures.append('麦克风开启结果未知，SDK 未返回可关闭的会话；请关闭或重启设备并重启 Application，不能仅凭重试宣称停止')
        failures.extend(self.shutdown_failures)
        self.state['stopStatus'] = 'unconfirmed' if failures else 'confirmed'
        self.robot_tools.faulted = bool(failures)
        self.robot_tools.set_permissions({key: False for key in self.robot_tools.permissions})
        if failures:
            self.state['error'] = '; '.join(failures)
        else:
            self.state['error'] = '' if not self.state['error'] else self.state['error']
        self.publish()

    async def stop(self, *, reason='user-stop') -> None:
        owner = asyncio.current_task()
        if self.stop_task is None:
            self.state['stopReason'] = reason
            self.lifecycle_epoch += 1
            if not self.lifecycle_task and not (self.cleanup_task and not self.cleanup_task.done()):
                self.shutdown_failures.clear()
                self.cleanup_task = None
                self.physical_stop_task = None
            if self.state['stopStatus'] == 'unconfirmed':
                self.state['error'] = ''
            self.stopping = self.cancel_latched = True
            self.state['stopStatus'] = 'stopping'
            if self.agent and hasattr(self.agent, 'invalidate_turn'):
                self.agent.invalidate_turn()
            self.publish()
            # The physical path is deliberately outside the lifecycle lock.
            if self.physical_stop_task is None:
                self.physical_stop_task = asyncio.create_task(self._physical_stop())
            if self.lifecycle_task and self.lifecycle_task is not owner:
                self.lifecycle_task.cancel()
            async def cleanup():
                try:
                    async with self.lock:
                        await self._cleanup(owner=owner)
                finally:
                    self.stop_task = None
            self.stop_task = asyncio.create_task(cleanup())
        task = self.stop_task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await drain_owned(task)
            raise

    async def mute(self, muted: bool) -> None:
        epoch = self.lifecycle_epoch
        if self.stopping:
            return
        self.muted = muted
        if muted:
            async with self.media_lock:
                await self._close_microphone()
        elif self.state['connected'] and not self.state['agentWorking'] and not self.state['speaking'] and not self.mic:
            async with self.media_lock:
                if epoch != self.lifecycle_epoch or self.stopping or not self.state['connected']:
                    return
                await self._open_microphone()
                if epoch != self.lifecycle_epoch or self.stopping or not self.state['connected']:
                    await self._close_microphone()
                    return
                self.mic_task = self._spawn(self._microphone)
        self.publish()

    @asynccontextmanager
    async def _robot_exclusive(self):
        async with self.media_lock:
            await self._close_microphone()
            try:
                yield
            finally:
                if self.state['connected'] and not self.state['agentWorking'] and not self.state['speaking'] and not self.stopping and not self.muted and not self.robot_tools.faulted:
                    await self._open_microphone()
                    self.mic_task = self._spawn(self._microphone)

    async def permissions(self, values):
        previous = dict(self.robot_tools.permissions)
        self.robot_tools.set_permissions(values)
        if any(previous[key] and not value for key, value in self.robot_tools.permissions.items()):
            await self.cancel_actions(reason='permission-revoked')

    async def cancel_actions(self, *, reason='user-cancel'):
        """End this session as well: untagged RTC audio cannot safely be reused
        after cancellation. The user explicitly starts a clean session next."""
        self.cancel_latched = True
        self.stopping = True
        if self.agent and hasattr(self.agent, 'invalidate_turn'):
            self.agent.invalidate_turn()
        await self.stop(reason=reason)

    async def _open_microphone(self) -> None:
        owner = asyncio.current_task()
        self.device_owners.add(owner)
        task = asyncio.create_task(asyncio.to_thread(self.robot.microphone.open_pcm, queue_size=16))
        try:
            try:
                self.mic = await asyncio.shield(task)
            except asyncio.CancelledError:
                # A blocking SDK call can complete after cancellation; retain and release its lease.
                self.mic = await drain_owned(task)
                raise
        except Exception:
            # The open may have applied before a lost/malformed ACK. Without a
            # returned SDK lease, absence of self.mic is not proof of closure.
            self.mic_open_uncertain = True
            raise
        finally:
            self.device_owners.discard(owner)

    async def _start_playback(self, pcm: bytes):
        task = asyncio.create_task(asyncio.to_thread(self.robot.audio.play_pcm, pcm))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Let stream creation finish before cleanup sends stop.
            await drain_owned(task)
            await drain_owned(asyncio.create_task(asyncio.to_thread(self.robot.audio.stop)))
            raise

    async def _close_microphone(self) -> None:
        if self.mic_task:
            self.mic_task.cancel()
            await asyncio.gather(self.mic_task, return_exceptions=True)
            self.mic_task = None
        if self.mic:
            task = asyncio.create_task(asyncio.to_thread(self.mic.close))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await drain_owned(task)
                self.mic = None
                raise
            self.mic = None
        self.publish()

    async def text(self, text: str) -> None:
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('请输入 1–4000 字的消息')
        if not self.agent or not self.state['connected']:
            raise RuntimeError('语音会话尚未连接')
        if self.stopping or self.state['stopStatus'] == 'unconfirmed' or self.robot_tools.faulted:
            raise RuntimeError('设备停止未确认或正在停止，不能发起新任务')
        if self.state['agentWorking'] or self.state['speaking'] or self.output.pending or not self.play_queue.empty():
            raise BusyRequest('Codex 正在处理任务，追加请求未执行。请等待完成后重试，或先停止。')
        self.cancel_latched = False
        self._accept_latency()
        self.state.update(agentWorking=True, stopStatus='idle', notice='收到，正在处理你的请求；动作会显示在右侧。')
        self.state['messages'].append(dict(id=uuid.uuid4().hex, role='user', content=text.strip()))
        self.state['messages'] = self.state['messages'][-100:]
        self.publish()
        try:
            await self._submit_task(self.agent, text.strip())
        except BaseException as error:
            self.state['agentWorking'] = False
            if not isinstance(error, asyncio.CancelledError) and not self.stopping:
                self.record_failure(error)
                await self.stop(reason='runtime-error')
            self.publish()
            raise

    async def _submit_task(self, agent, text):
        epoch = self.lifecycle_epoch
        async with self.media_lock:
            await self._close_microphone()
            if self.stopping or epoch != self.lifecycle_epoch or self.agent is not agent:
                return
            await self.body_feedback.show('thinking')
        if self.stopping or epoch != self.lifecycle_epoch or self.agent is not agent:
            return
        # Keep a fresh, isolated output identity, but pay RTC setup in parallel
        # with reasoning/tools rather than only after the final answer exists.
        if hasattr(agent, 'prepare_speech'):
            async def prepare():
                voice_epoch = await agent.prepare_speech()
                if self.stopping or epoch != self.lifecycle_epoch or self.agent is not agent:
                    return
                self.prepared_voice_epoch = voice_epoch
                self._mark_latency('speechReady')
            self.prepared_voice_epoch = None
            self.speech_prepare_task = self._spawn(prepare)
        await agent.text(text)

    async def _restore_listening(self):
        epoch = self.lifecycle_epoch
        async with self.media_lock:
            if (self.stopping or epoch != self.lifecycle_epoch or not self.state['connected']
                    or self.state['agentWorking'] or self.state['speaking'] or self.robot_tools.faulted):
                return
            await self.body_feedback.close()
            if self.stopping or epoch != self.lifecycle_epoch or not self.state['connected']:
                return
            if not self.muted and not self.mic:
                await self._open_microphone()
                if self.stopping or epoch != self.lifecycle_epoch or not self.state['connected']:
                    await self._close_microphone()
                    return
                self.mic_task = self._spawn(self._microphone)
            self.publish()

    async def test_device(self, duration: float = 3) -> None:
        """Capture actual robot microphone PCM then replay it; never label as agent output."""
        epoch = self.lifecycle_epoch
        if self.state['stopStatus'] == 'stopping':
            raise asyncio.CancelledError()
        async with self.lock:
            if epoch != self.lifecycle_epoch:
                raise asyncio.CancelledError()
            if self.state['stopStatus'] == 'unconfirmed' or self.robot_tools.faulted:
                raise RuntimeError('上次停止未确认，请先重试停止并检查设备')
            if self.state['connected']:
                raise RuntimeError('请先结束语音会话，再测试设备音频')
            self.state.update(busy=True, error='', deviceTest=None)
            self.lifecycle_task = asyncio.current_task()
            self.stopping = self.cancel_latched = False
            self.physical_stop_task = None
            self.cleanup_task = None
            self.publish()
            try:
                await self.refresh_device()
                if not self.state['deviceOnline']:
                    raise RuntimeError('机器人未连接')
                await self._open_microphone()
                self.publish()
                pcm = bytearray()
                frames = 0
                deadline = asyncio.get_running_loop().time() + duration
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        frame = await asyncio.to_thread(self.mic.read, timeout=.2)
                    except TimeoutError:
                        continue
                    pcm.extend(frame.data)
                    frames += 1
                    if len(pcm) > 32000 * (duration + 1):
                        raise RuntimeError('麦克风采样数据超过预期时长')
                await asyncio.to_thread(self.mic.close)
                self.mic = None
                if not pcm:
                    raise RuntimeError('设备麦克风没有返回音频帧')
                buffer = OutputBuffer()
                for offset in range(0, len(pcm), 32000):
                    buffer.add(audio_params('device-test', pcm[offset:offset + 32000])['audio'])
                self.state['speaking'] = True
                self.publish()
                output = buffer.take()
                job = await self._start_playback(output)
                await asyncio.to_thread(job.wait, timeout=len(output) / 48000 + 10)
                samples = [sample[0] for sample in struct.iter_unpack('<h', pcm)]
                self.state['deviceTest'] = dict(frames=frames, inputBytes=len(pcm),
                    rms=round(math.sqrt(sum(s * s for s in samples) / len(samples)), 1),
                    playbackCompleted=True)
            except BaseException as error:
                if not isinstance(error, asyncio.CancelledError):
                    self.record_failure(error)
                raise
            finally:
                await self._cleanup()
                self.lifecycle_task = None

    async def _microphone(self) -> None:
        while self.mic and self.agent:
            try:
                frame = await asyncio.to_thread(self.mic.read, timeout=.2)
            except TimeoutError:
                continue
            except Exception:
                if self.stopping or self.cancel_latched:
                    return
                raise
            if self.stopping or self.cancel_latched or self.muted or self.state['agentWorking'] or self.state['speaking'] or self.output.pending or not self.play_queue.empty():
                continue
            # Decoder flush may produce more than one second; preserve sample boundaries.
            for offset in range(0, len(frame.data), 32000):
                await self.agent.append_audio(frame.data[offset:offset + 32000])
            self.state['micFrames'] += 1
            if self.state['micFrames'] % 10 == 0:
                self.publish()

    async def _speak_answer(self, answer, turn_id):
        # A realtime model can violate a silence instruction. Drop unsolicited
        # voice and wait for its acoustic tail before admitting the task answer.
        while asyncio.get_running_loop().time() - self.last_upstream_audio_at < self.output_gap:
            await asyncio.sleep(.1)
            if self.cancel_latched or self.current_turn != turn_id:
                return
        if self.cancel_latched or self.current_turn != turn_id or not self.agent:
            return
        if hasattr(self.agent, 'prepare_speech'):
            self.state['speechStatus'] = 'connecting'
            self.publish()
            if self.speech_prepare_task:
                await asyncio.shield(self.speech_prepare_task)
                self.answer_voice_epoch = self.prepared_voice_epoch
            else:
                self.answer_voice_epoch = await self.agent.prepare_speech()
                self._mark_latency('speechReady')
            if self.cancel_latched or self.current_turn != turn_id:
                return
        self.transcript_ids.pop('assistant', None)
        self.answer_turn = turn_id
        self.answer_received_bytes = self.answer_played_bytes = 0
        self.answer_audio_format = None
        await self.agent.speak(answer)
        if self.turn_deadline:
            self.turn_deadline.voice_requested(self._now())
        self.state['speechStatus'] = 'requested'
        self._mark_latency('speechRequested')
        self.publish()

    def _voice_key(self, epoch=None):
        if epoch is None:
            epoch = getattr(self.agent, 'voice_epoch', None)
        # Handoff and finalized ASR can refine the same utterance differently.
        # Text equality is not its identity; new speech boundaries advance generation.
        return (epoch, self.voice_generation)

    def _commit_voice(self, text, epoch=None):
        text = text.strip()[:4000]
        key = self._voice_key(epoch)
        if not text or key in self.voice_commits or self.stopping or self.muted or not self.agent:
            return False
        self.voice_commits.add(key)
        if len(self.voice_commits) > 64:
            self.voice_commits = {key}
        if self.state['agentWorking'] or self.state['speaking'] or self.output.pending or not self.play_queue.empty():
            self.state['notice'] = '任务处理中收到的额外语音提交未执行。如需新任务，请在完成后重新说一次。'
            self.publish()
            return False
        agent = self.agent
        self._accept_latency()
        self.state.update(agentWorking=True, stopStatus='idle', error='', notice='收到，正在处理你的请求；动作会显示在右侧。')
        self.state['messages'].append(dict(id=uuid.uuid4().hex, role='user', content=text))
        self.state['messages'] = self.state['messages'][-100:]
        self._spawn(lambda: self._submit_task(agent, text))
        self.publish()
        return True

    async def _events(self) -> None:
        while self.agent and not self.stopping:
            self.agent_event_inflight = False
            try:
                event = await asyncio.wait_for(self._receive_agent_event(), self.output_gap)
            except asyncio.TimeoutError:
                if self.output.pending:
                    self._queue_audio()
                self._finish_answer()
                continue
            method, params = event.get('method'), event.get('params', {})
            if (params.get('voiceEpoch') is not None
                    and params['voiceEpoch'] != getattr(self.agent, 'voice_epoch', None)):
                continue
            if (self.turn_deadline and params.get('turnId') == self.current_turn
                    and method in {'item/started', 'item/completed', 'item/agentMessage/delta'}):
                self.turn_deadline.task_progress(self._now())
            if method not in {'thread/realtime/outputAudio/delta', 'thread/realtime/transcript/delta'}:
                self.state['agentEvents'].append(method)
                self.state['agentEvents'] = self.state['agentEvents'][-30:]
            if method == 'local/toolImagesAttached' and params.get('turnId') == self.current_turn:
                self.state['lastPhotoDelivery'] = params
                for task in self.robot_tools.tasks:
                    if (task.get('callId') == params.get('callId') and task.get('turnId') == self.current_turn
                            and task['status'] == 'attaching'):
                        task.update(status='analyzing', detail=f"本轮 {len(params['photos'])} 张照片已附入同一 Codex 任务，等待分析")
                self.publish()
            if method == 'local/toolImagesRejected' and params.get('turnId') == self.current_turn:
                for task in self.robot_tools.tasks:
                    if task.get('callId') == params.get('callId') and task['status'] == 'attaching':
                        task.update(status='failed', detail=params['message'])
                self.publish()
            if method == 'local/delegation' and isinstance(params.get('text'), str):
                # V3 client handoff carries the finalized input transcript.
                # It is another commit signal, never a second task brain.
                if self.muted or self.cancel_latched:
                    continue
                identity = (params.get('voiceEpoch'), params.get('id'))
                if params.get('id') and identity in self.voice_handoffs:
                    continue
                if params.get('id'):
                    if (not self.voice_draft_active and
                            (self.voice_has_handoff or self._voice_key(params.get('voiceEpoch')) not in self.voice_commits)):
                        self.voice_generation += 1
                    self.voice_has_handoff = True
                    self.voice_handoffs.add(identity)
                    if len(self.voice_handoffs) > 64:
                        self.voice_handoffs = {identity}
                self.voice_draft_active = False
                if self._commit_voice(params['text'], params.get('voiceEpoch')):
                    self.state['lastVoiceInput'] = dict(source='client-handoff', epoch=params.get('voiceEpoch'))
                    self.publish()
            if method == 'turn/started':
                if self.cancel_latched:
                    continue
                self.current_turn = params.get('turn', {}).get('id', '')
                self._mark_latency('agentStarted')
                self.state['speechStatus'] = 'idle'
                self.answer_done = False
                self.state['agentWorking'] = True
                self.state['agentTurns'] += 1
                if self.turn_watchdog:
                    self.turn_watchdog.cancel()
                turn_id = self.current_turn
                deadline = self.turn_deadline = TurnDeadline(self._now())
                self.turn_watchdog = self._spawn(lambda turn_id=turn_id, deadline=deadline: self._watch_turn(turn_id, deadline))
                self.publish()
            if method == 'turn/completed':
                turn = params.get('turn', {})
                if self.cancel_latched or turn.get('id') != self.current_turn:
                    continue
                self.state['agentWorking'] = turn.get('status') == 'completed'
                if self.turn_deadline:
                    if turn.get('status') == 'completed':
                        self.turn_deadline.begin_voice(self._now())
                    else:
                        self.turn_deadline.finish()
                if turn.get('status') == 'failed':
                    self.state['error'] = turn.get('error', {}).get('message', 'Codex 任务失败')
                for task in self.robot_tools.tasks:
                    if task['status'] in {'attaching', 'analyzing'} and task.get('turnId') == self.current_turn:
                        task.update(status='failed' if turn.get('status') == 'failed' else
                                    'cancelled' if turn.get('status') == 'interrupted' else 'answering',
                                    detail='等待真实语音播报' if turn.get('status') == 'completed' else '任务未完成')
                if turn.get('status') == 'completed' and not self.cancel_latched:
                    self._mark_latency('answerReady')
                    answer = '\n'.join(item['text'] for item in turn.get('items', [])
                        if item.get('type') == 'agentMessage' and item.get('phase') in {None, 'final_answer'} and item.get('text'))
                    if answer:
                        display_answer = answer if len(answer) <= 16000 else answer[:15940] + '\n[回答超过16000字，页面已截断显示。]'
                        self.state['lastAgentAnswer'] = display_answer
                        self.state['messages'].append(dict(id=uuid.uuid4().hex, role='assistant', content=display_answer))
                        self.state['messages'] = self.state['messages'][-100:]
                        # V3 accepts at most 500 UTF-8 bytes per speakable chunk.
                        # Keep one utterance; transcript done is not media EOS.
                        spoken = answer
                        if len(spoken.encode('utf-8')) > 500:
                            spoken = spoken.encode('utf-8')[:440].decode('utf-8', errors='ignore') + '。详细内容请看页面。'
                        turn_id = self.current_turn
                        self._spawn(lambda: self._speak_answer(spoken, turn_id))
                    else:
                        self.state['agentWorking'] = False
                        self.state['error'] = 'Codex 本轮没有最终回答，语音播报未完成'
                        for task in self.robot_tools.tasks:
                            if task.get('turnId') == self.current_turn and task['status'] == 'answering':
                                task.update(status='failed', detail=self.state['error'])
                        raise RuntimeError(self.state['error'])
                elif turn.get('status') in {'failed', 'interrupted'}:
                    self.state['error'] = self.state['error'] or 'Codex任务已中断，本轮未完成'
                    await self._restore_listening()
                self.publish()
            if method in {'thread/realtime/error', 'local/error'}:
                raise RuntimeError(params.get('message', 'Codex 语音连接失败'))
            if method == 'error':
                self.state['error'] = params.get('error', {}).get('message', 'Codex 后台任务发生错误')
                self.publish()
            if method == 'thread/realtime/closed':
                raise RuntimeError('Codex 语音会话已关闭')
            if method == 'thread/realtime/outputAudio/delta':
                if params.get('voiceEpoch') != self.answer_voice_epoch:
                    continue
                self.last_upstream_audio_at = asyncio.get_running_loop().time()
                if not self.cancel_latched and self.answer_turn:
                    self._mark_latency('firstAudio')
                    if self.turn_deadline:
                        self.turn_deadline.audio_progress(self._now())
                    self.last_audio_at = self._now()
                    self.state['speechStatus'] = 'streaming'
                    before = len(self.output.data)
                    self.output.add(params['audio'])
                    if self.answer_audio_format is not None and self.output.format != self.answer_audio_format:
                        raise ValueError('本轮语音在分段后改变了PCM格式')
                    self.answer_audio_format = self.output.format
                    self.answer_received_bytes += len(self.output.data) - before
                    rate, channels = self.output.format
                    if len(self.output.data) >= rate * channels * 2 * 2:
                        self._queue_audio()  # Two seconds, not the whole utterance.
            if method in {'thread/realtime/transcript/delta', 'thread/realtime/transcript/done'}:
                role = params.get('role')
                if role not in {'user', 'assistant'}:
                    continue
                if self.cancel_latched:
                    continue
                if role == 'user':
                    if self.muted:
                        continue
                    if method.endswith('/delta'):
                        if not self.voice_draft_active:
                            self.voice_generation += 1
                            self.voice_draft_active = True
                            self.voice_has_handoff = False
                        continue  # Draft speech is not an executed user request.
                    self.voice_draft_active = False
                    if self._commit_voice(params.get('text', ''), params.get('voiceEpoch')):
                        self.state['lastVoiceInput'] = dict(source='transcript-done', epoch=params.get('voiceEpoch'))
                        self.publish()
                    continue
                if role == 'assistant' and not self.answer_turn:
                    continue
                if role == 'assistant' and params.get('voiceEpoch') != self.answer_voice_epoch:
                    continue
                if role == 'assistant':
                    if method.endswith('/done'):
                        self.answer_done = True
                        self.state['lastSpokenTranscript'] = params.get('text', '')[:16000]
                        self.publish()
                    continue  # Preserve the task agent's complete text in UI.

    async def _receive_agent_event(self):
        event = await self.agent.events.get()
        # Mark in the getter task, before wait_for resumes its parent. This
        # covers Python 3.10's queue-empty / consumer-not-resumed handoff gap.
        self.agent_event_inflight = True
        return event

    def _queue_audio(self):
        try:
            self.play_queue.put_nowait(self.output.take())
        except asyncio.QueueFull as error:
            raise RuntimeError('设备播放跟不上语音输出，会话已停止') from error

    def _agent_event_pending(self):
        return self.agent_event_inflight or bool(self.agent and not self.agent.events.empty())

    def _finish_answer(self, now=None):
        # Data-channel done is not RTP EOS, in either order. Batching silence
        # must never certify completion. Keep one conservative tail window,
        # with the absolute voice budget and every device receipt still required.
        now = self._now() if now is None else now
        if (not self.cancel_latched and self.answer_turn and self.answer_done
                and self.answer_received_bytes > 0 and self.answer_played_bytes > 0
                and not self.output.pending and self.play_queue.empty() and not self.state['speaking']
                and not self._agent_event_pending()
                and (not self.turn_deadline or self.turn_deadline.can_finish(now))):
            if now - self.last_audio_at < VOICE_IDLE_SECONDS:
                if self.state.get('speechStatus') != 'settling':
                    self.state['speechStatus'] = 'settling'
                    self.publish()
                return
            for task in self.robot_tools.tasks:
                if task['status'] == 'answering' and task.get('turnId') == self.answer_turn:
                    task.update(status='completed', detail='本轮拍摄已完成，已收到的语音已在设备播完，收尾完成；是否看清请以回答为准')
            self.answer_turn = ''
            if self.turn_deadline:
                self.turn_deadline.finish()
            self.state['agentWorking'] = False
            self.state['speechStatus'] = 'completed'
            self.state['notice'] = ''
            if self.state['connected']:
                self._spawn(self._restore_listening)
            self.publish()

    async def _playback(self) -> None:
        while True:
            pcm = await self.play_queue.get()
            if self.cancel_latched:
                continue
            self.state['speaking'] = True
            self.publish()
            # The device codec is exclusive: upload gating alone still owns RX.
            async with self._robot_exclusive():
                await self.body_feedback.show('speaking')
                job = await self._start_playback(pcm)
                self._mark_latency('firstPlaybackJob')
                if self.turn_deadline:
                    self.turn_deadline.playback_started(self._now())
                wait = asyncio.create_task(asyncio.to_thread(job.wait, timeout=len(pcm) / 48000 + 10))
                try:
                    await asyncio.shield(wait)
                except JobCancelledError:
                    if not (self.stopping or self.cancel_latched):
                        raise
                    return  # Device confirmed the requested abort, not a cleanup failure.
                except asyncio.CancelledError:
                    await drain_owned(asyncio.create_task(asyncio.to_thread(self.robot.audio.stop)))
                    try:
                        await drain_owned(wait)
                    except JobCancelledError:
                        if not (self.stopping or self.cancel_latched):
                            raise
                        # Only this exact terminal receipt confirms intentional abort.
                    raise
                self.state['outputBytes'] += len(pcm)
                self._mark_latency('firstPlaybackReceipt')
                self.answer_played_bytes += len(pcm)
                if self.turn_deadline:
                    self.turn_deadline.playback_finished(self._now())
            # Keep upload paused briefly for acoustic tail; discard mic frames in the meantime.
            if not self.state['agentWorking']:
                await asyncio.sleep(.3)
            self.state['speaking'] = False
            self._finish_answer()
            if not self.state['agentWorking']:
                await self._restore_listening()
            self.publish()
