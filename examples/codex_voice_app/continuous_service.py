"""Persistent voice frontend on native Device RTC, not legacy PCM jobs.

Conversation audio, background task admission, and physical cancellation have
separate lifetimes. No transcript is automatically a robot command.
"""
from __future__ import annotations

import asyncio
import base64
import math
import struct
from contextlib import asynccontextmanager
import uuid

from async_utils import drain_owned
from audio_bridge import OutputBuffer
from body_feedback import SuppressedBodyFeedback
from codex_agent import CodexAgent
from playback_gain import PlaybackGain
from service import VoiceService
from turn_deadline import TurnDeadline


class ContinuousVoiceService(VoiceService):
    def __init__(self, robot, device_online, rtc, agent_factory=None, *,
                 device_peer_factory=None, **kwargs):
        super().__init__(robot, device_online,
                         agent_factory or (lambda: CodexAgent(realtime=True)), **kwargs)
        self.rtc = rtc
        self.device_peer_factory = device_peer_factory
        self.device_peer = None
        self.body_feedback = SuppressedBodyFeedback(robot)
        self.delegation_id = None
        self.handoffs = set()
        self.last_progress_detail = None
        self.playback_gain = PlaybackGain(sample_rate=48000)
        self.result_handoff_task = self.progress_task = None
        self.result_handoff_turn = None
        self.maintenance_at = 0.0
        self.media_forwarding = dict(eventResidenceMs=0.0, eventResidenceMaxMs=0.0)
        self.state.update(voiceMode='realtime', speechStatus='idle')

    def snapshot(self):
        snapshot = super().snapshot()
        snapshot['duplex'] = dict(transport='webrtc', deviceTransport='webrtc',
            microphoneConcurrent=True, echoCancellation='unverified', expressionsEnabled=False)
        snapshot['deviceRtc'] = self.device_peer.diagnostics() if self.device_peer else self.state.get('deviceRtc', {})
        snapshot['playbackGain'] = self.playback_gain.diagnostics()
        snapshot['mediaForwarding'] = dict(self.media_forwarding)
        return snapshot

    async def _open_microphone(self):
        if not self.robot.supports('rtc.audio.full_duplex.v1'):
            raise RuntimeError('设备未协商 rtc.audio.full_duplex.v1，不回退到互斥PCM通道')
        # This hook runs only after base start admits a new lifecycle under its
        # lock. Repeated/concurrent start must not erase active delegation state.
        self.playback_gain = PlaybackGain(sample_rate=48000)
        self.result_handoff_task = self.progress_task = None
        self.result_handoff_turn = None
        self.maintenance_at = 0.0
        self.media_forwarding = dict(eventResidenceMs=0.0, eventResidenceMaxMs=0.0)
        self.handoffs.clear()
        self.delegation_id = None
        if self.device_peer_factory is None:
            from device_rtc_peer import DeviceRtcPeer
            factory = DeviceRtcPeer
        else:
            factory = self.device_peer_factory
        peer = self.device_peer = factory(self.rtc, self.agent.events)
        task = asyncio.create_task(peer.start())
        try:
            await asyncio.shield(task)
            self.mic = peer  # Presence is the native capture lease, not a PCM session.
        except asyncio.CancelledError:
            try:
                await drain_owned(task)
            finally:
                await drain_owned(asyncio.create_task(peer.close()))
            raise
        except BaseException:
            await peer.close()
            raise

    async def _microphone(self):
        # DeviceRtcPeer receives/paces native media. Do not open a second lease.
        while self.mic and not self.stopping:
            await asyncio.sleep(.5)

    async def _playback(self):
        # RTC output is forwarded frame-by-frame in _events, never SDK PCM jobs.
        await asyncio.Future()

    async def _physical_stop(self):
        operations = []
        if self.device_peer:
            operations.append(self.device_peer.close())
        if hasattr(self.robot, 'motion'):
            operations.append(self.robot_tools.cancel())
        results = await asyncio.gather(*operations, return_exceptions=True)
        failures = [str(value) or type(value).__name__ for value in results if isinstance(value, BaseException)]
        if not failures:
            self.mic = None
        return failures

    async def _cleanup_impl(self, owner):
        # Base cleanup drains every owner before the late-start compensation.
        # A native peer is async, never pass its close to the legacy sync path.
        self.mic = None
        await super()._cleanup_impl(owner)
        if self.device_peer:
            self.state['deviceRtc'] = self.device_peer.diagnostics()
            try:
                await self.device_peer.close()
            except Exception as error:
                self.state.update(stopStatus='unconfirmed', error=str(error))
                self.robot_tools.faulted = True
            else:
                self.device_peer = None
        self.delegation_id = None
        self.handoffs.clear()
        self.publish()

    async def mute(self, muted):
        if self.stopping:
            return
        self.muted = muted
        # AEC capture must continue, but muted input is not sent to Codex.
        if muted and self.agent and getattr(self.agent, 'peer', None):
            self.agent.peer.track.buffer.clear()
        self.publish()

    @asynccontextmanager
    async def _robot_exclusive(self):
        # Native RTC audio has its own full-duplex lease. Camera/motion retain
        # their SDK authority and resource admission; never steal audio leases.
        yield

    async def _restore_listening(self):
        return  # It never stopped listening while reasoning or speaking.

    async def text(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('请输入 1–4000 字的消息')
        if not self.agent or not self.state['connected'] or self.stopping:
            raise RuntimeError('实时语音会话尚未连接或正在停止')
        self._accept_latency()
        self._message('user', text.strip())
        await self.agent.frontend_text(text.strip())
        self.publish()

    def _message(self, role, text):
        text = text.strip()[:16000]
        if not text:
            return
        self.state['messages'].append(dict(id=uuid.uuid4().hex, role=role, content=text))
        self.state['messages'] = self.state['messages'][-100:]

    async def _submit_task(self, agent, text):
        if not self.stopping and self.agent is agent:
            await agent.text(text)  # No close-mic, facial feedback, or RTC rotation.

    def _finish_answer(self, now=None):
        return  # Transcript done/silence are not physical audio completion receipts.

    async def _delegate(self, params):
        identity = params.get('id')
        text = params.get('text')
        if (not isinstance(identity, str) or not identity or not isinstance(text, str)
                or not text.strip() or len(text) > 4000 or self.muted or self.stopping):
            return
        if identity in self.handoffs:
            return
        if len(self.handoffs) >= 256:
            raise RuntimeError('实时会话委派预算已用尽，请结束后重开')
        self.handoffs.add(identity)
        if self.state['agentWorking']:
            agent = self.agent
            self._spawn(lambda: agent.frontend_context(
                '后台仍在处理上一项任务；此追加动作未执行。停止动作请用页面按钮。',
                delegation_id=identity, channel='speakable'))
            return
        self.delegation_id = identity
        self.last_progress_detail = None
        self._accept_latency()
        self.state.update(agentWorking=True, error='', notice='语音前台持续在线，后台正在执行受限任务。')
        self.state['lastVoiceInput'] = dict(source='client-handoff', epoch=params.get('voiceEpoch'))
        self._spawn(lambda: self._submit_task(self.agent, text.strip()))
        self.publish()

    async def _task_completed(self, turn):
        if turn.get('id') != self.current_turn:
            return
        identity = self.delegation_id
        succeeded = turn.get('status') == 'completed'
        answer = '\n'.join(item['text'] for item in turn.get('items', [])
            if item.get('type') == 'agentMessage' and item.get('phase') in {None, 'final_answer'} and item.get('text'))
        succeeded = succeeded and bool(answer)
        if not succeeded:
            answer = turn.get('error', {}).get('message') or '后台任务未完成，不能确认动作或观察结果。'
            self.state['error'] = answer
        else:
            self.state['lastAgentAnswer'] = answer[:16000]
            self._mark_latency('answerReady')
        for task in self.robot_tools.tasks:
            if task.get('turnId') == self.current_turn and task['status'] in {'attaching', 'analyzing', 'answering'}:
                task.update(status='completed' if succeeded else 'failed',
                            detail='真实图像任务已完成；结果交给持续语音前台，不等同设备已播完' if succeeded else answer)
        if self.turn_deadline:
            self.turn_deadline.finish()
        # Keep the admission slot until context handoff finishes.
        await self.agent.frontend_context(answer, delegation_id=identity, channel='speakable')
        self.delegation_id = None
        self.state.update(agentWorking=False, notice='')
        self.publish()

    async def _progress(self):
        for task in reversed(self.robot_tools.tasks):
            if task.get('turnId') == self.current_turn and task['detail'] != self.last_progress_detail:
                self.last_progress_detail = task['detail']
                await self.agent.frontend_context('后台实际进度：' + task['detail'],
                    delegation_id=self.delegation_id, channel='commentary')
                return

    async def _events(self):
        while self.agent and not self.stopping:
            now = self._now()
            # Continuous 50 Hz audio means queue.get rarely times out. UI and
            # progress maintenance must not depend on that queue being idle.
            if now >= self.maintenance_at:
                self.maintenance_at = now + .15
                if self.state['speaking'] and now - self.last_audio_at > .4:
                    self.state['speaking'] = False
                    self.publish()  # Activity only, not a playback receipt.
                if (self.state['agentWorking'] and self.delegation_id
                        and not (self.result_handoff_task and not self.result_handoff_task.done())
                        and not (self.progress_task and not self.progress_task.done())):
                    self.progress_task = self._spawn(self._progress)
            self.agent_event_inflight = False
            try:
                event = await asyncio.wait_for(self._receive_agent_event(), .15)
            except asyncio.TimeoutError:
                continue
            method, params = event.get('method'), event.get('params', {})
            if params.get('voiceEpoch') is not None and params['voiceEpoch'] != self.agent.voice_epoch:
                continue
            if method == 'local/deviceAudio':
                enqueued = params.get('timing', {}).get('enqueuedAt')
                if isinstance(enqueued, (int, float)) and not isinstance(enqueued, bool) and math.isfinite(enqueued):
                    residence = max(0.0, (self._now() - enqueued) * 1000)
                    self.media_forwarding.update(eventResidenceMs=round(residence, 2),
                        eventResidenceMaxMs=round(max(self.media_forwarding['eventResidenceMaxMs'], residence), 2))
                if not self.muted:
                    pcm = base64.b64decode(params['audio']['data'], validate=True)
                    for offset in range(0, len(pcm), 32000):
                        await self.agent.append_audio(pcm[offset:offset + 32000])
                    self.state['micFrames'] += 1
                    if self.state['speaking']:
                        self.state['duplexConcurrentFrames'] = self.state.get('duplexConcurrentFrames', 0) + 1
                    if self.state['micFrames'] % 25 == 0:
                        self.publish()
            elif method == 'thread/realtime/outputAudio/delta':
                if type(params.get('voiceEpoch')) is not int:
                    raise ValueError('Native RTC output requires a voice epoch')
                if type(params['audio'].get('samplesPerChannel')) is not int:
                    raise ValueError('Native RTC output requires a sample count')
                audio = OutputBuffer(max_seconds=1)
                audio.add(params['audio'])
                if audio.format != (48000, 1):
                    raise ValueError('Native RTC requires 48 kHz mono output PCM')
                pcm = audio.take(sample_rate=48000)
                peer, gain, epoch = self.device_peer, self.playback_gain, self.lifecycle_epoch
                # A bounded large event must not monopolize the audio/control
                # loop. Stop can intervene between 20 ms processing slices.
                for offset in range(0, len(pcm), 1920):
                    if self.stopping or self.lifecycle_epoch != epoch or self.device_peer is not peer:
                        break
                    await peer.append_audio(gain.process(pcm[offset:offset + 1920]), sample_rate=48000)
                    await asyncio.sleep(0)
                else:
                    if self.stopping or self.lifecycle_epoch != epoch or self.device_peer is not peer:
                        continue
                    # Quiet media advances the envelope without showing endless
                    # speaking, counting replies or renewing first-audio latency.
                    if any(abs(v) > 64 for (v,) in struct.iter_unpack('<h', pcm)):
                        self.last_audio_at = self._now()
                        self.state['speaking'] = True
                        self.state['outputBytes'] += len(pcm)
                        self._mark_latency('firstAudio')
            elif method in {'thread/realtime/transcript/delta', 'thread/realtime/transcript/done'}:
                if params.get('role') in {'user', 'assistant'} and not (self.muted and params['role'] == 'user'):
                    role = params['role']
                    item_id = self.transcript_ids.get(role)
                    item = next((item for item in self.state['messages'] if item['id'] == item_id), None)
                    if item is None:
                        item = dict(id=uuid.uuid4().hex, role=role, content='')
                        self.state['messages'].append(item)
                        self.state['messages'] = self.state['messages'][-100:]
                        self.transcript_ids[role] = item['id']
                    if method.endswith('/done'):
                        item['content'] = params.get('text', '')[:16000]
                        self.transcript_ids.pop(role, None)
                    else:
                        item['content'] = (item['content'] + params.get('delta', ''))[:16000]
                    self.publish()
            elif method == 'local/delegation':
                await self._delegate(params)
            elif method == 'turn/started':
                self.current_turn = params.get('turn', {}).get('id', '')
                self.state['agentTurns'] += 1
                self._mark_latency('agentStarted')
                deadline = self.turn_deadline = TurnDeadline(self._now())
                if self.turn_watchdog:
                    self.turn_watchdog.cancel()
                turn_id = self.current_turn
                self.turn_watchdog = self._spawn(lambda: self._watch_turn(turn_id, deadline))
                self.publish()
            elif method == 'turn/completed':
                turn = params.get('turn', {})
                if (turn.get('id') == self.current_turn
                        and turn.get('id') != self.result_handoff_turn):
                    self.result_handoff_turn = turn.get('id')
                    self.result_handoff_task = self._spawn(lambda turn=turn: self._task_completed(turn))
            elif method == 'local/toolImagesAttached' and params.get('turnId') == self.current_turn:
                self.state['lastPhotoDelivery'] = params
                for task in self.robot_tools.tasks:
                    if task.get('callId') == params.get('callId') and task['status'] == 'attaching':
                        task.update(status='analyzing', detail='本轮真实照片已附入后台，正在分析')
                self.publish()
            elif method in {'local/error', 'thread/realtime/error', 'thread/realtime/closed'}:
                raise RuntimeError(params.get('message') or '实时语音连接关闭')
            elif method == 'error':
                self.state['error'] = params.get('error', {}).get('message', 'Codex后台错误')
                self.publish()
            if self.turn_deadline and params.get('turnId') == self.current_turn and method.startswith('item/'):
                self.turn_deadline.task_progress(self._now())
