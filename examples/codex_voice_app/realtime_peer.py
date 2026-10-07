"""Codex WebRTC adapter, native 48k or legacy utterance-oriented 24k output."""
from __future__ import annotations

import asyncio
import base64
import copy
from fractions import Fraction
import json
import hashlib
import math
import struct
import time
from collections import deque

import av
from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from protocol import VOICE_INSTRUCTIONS, REALTIME_INSTRUCTIONS
from errors import error_message


def normalize_data_event(event: dict) -> dict | None:
    """Normalize V3 transcript events; media audio arrives only through RTP."""
    kind = event.get('type')
    if kind in {'input_transcript.added', 'output_transcript.added'}:
        text = event.get('item', {}).get('text')
        if isinstance(text, str):
            return {'method': 'thread/realtime/transcript/delta', 'params': {
                'role': 'user' if kind.startswith('input') else 'assistant', 'delta': text}}
    elif kind == 'turn.done':
        turn = event.get('turn', {})
        if turn.get('role') in {'user', 'assistant'} and isinstance(turn.get('transcript'), str):
            return {'method': 'thread/realtime/transcript/done', 'params': {
                'role': turn['role'], 'text': turn['transcript']}}
    elif kind == 'error':
        return {'method': 'local/error', 'params': {
            'message': event.get('error', {}).get('message', 'Codex realtime data channel failed')}}
    elif kind in {'session.started', 'session.updated'}:
        return {'method': 'local/rtcReady', 'params': {'type': kind}}
    elif kind == 'delegation.created':
        item = event.get('item', {})
        if item.get('type') == 'delegation' and item.get('target') == 'client':
            return {'method': 'local/delegation', 'params': {
                'id': item.get('id'), 'text': ''.join(c.get('text', '') for c in item.get('content', []) if c.get('type') == 'input_text')}}
    return None


class RobotAudioTrack(MediaStreamTrack):
    kind = 'audio'

    def __init__(self, *, clock=time.monotonic):
        super().__init__()
        self.buffer = bytearray()
        self.samples = 0
        self.started: float | None = None
        self.clock = clock
        self.arrivals = deque()
        self.buffer_high_water_ms = self.queue_residence_ms = self.queue_residence_max_ms = 0.0
        self.underflow_frames = 0
        self.freshness_dropped_bytes = self.freshness_recovery_count = 0

    def push(self, pcm: bytes) -> None:
        if not pcm or len(pcm) % 2:
            raise ValueError('Expected PCM s16le mono samples')
        if len(self.buffer) + len(pcm) > 32000:
            raise RuntimeError('WebRTC microphone backlog exceeds one second')
        if not self.buffer:
            self.arrivals.clear()  # Logical mute may clear the public PCM buffer.
        self.arrivals.append([len(pcm), self.clock()])
        self.buffer.extend(pcm)
        self.buffer_high_water_ms = max(self.buffer_high_water_ms, len(self.buffer) / 32)

    def diagnostics(self):
        return dict(bufferMs=round(len(self.buffer) / 32, 2),
                    bufferHighWaterMs=round(self.buffer_high_water_ms, 2),
                    queueResidenceMs=round(self.queue_residence_ms, 2),
                    queueResidenceMaxMs=round(self.queue_residence_max_ms, 2),
                    underflowFrames=self.underflow_frames,
                    freshnessDroppedMs=self.freshness_dropped_bytes / 32,
                    freshnessRecoveryCount=self.freshness_recovery_count,
                    maxQueuedAudioMs=120)

    def _consume_arrivals(self, consumed):
        while consumed and self.arrivals:
            take = min(consumed, self.arrivals[0][0])
            self.arrivals[0][0] -= take
            consumed -= take
            if not self.arrivals[0][0]:
                self.arrivals.popleft()

    def _recover_freshness(self):
        dropped = 0
        # Preserve a continuous RTP timeline, but never replay a growing FIFO
        # of old microphone speech. Loss is counted, not advertised as ASR speed.
        while self.buffer and (len(self.buffer) > 3840 or
                (self.arrivals and self.clock() - self.arrivals[0][1] > .120)):
            take = min(640, len(self.buffer))
            del self.buffer[:take]
            self._consume_arrivals(take)
            dropped += take
        if dropped:
            self.freshness_dropped_bytes += dropped
            self.freshness_recovery_count += 1

    async def recv(self):
        loop = asyncio.get_running_loop()
        if self.started is None:
            self.started = loop.time()
        await asyncio.sleep(max(0, self.started + self.samples / 16000 - loop.time()))
        self._recover_freshness()
        pcm = bytes(self.buffer[:640])
        del self.buffer[:640]
        consumed = len(pcm)
        if consumed and self.arrivals:
            self.queue_residence_ms = max(0.0, (self.clock() - self.arrivals[0][1]) * 1000)
            self.queue_residence_max_ms = max(self.queue_residence_max_ms, self.queue_residence_ms)
            self._consume_arrivals(consumed)
        if len(pcm) < 640:
            self.underflow_frames += 1
        if not self.buffer:
            self.arrivals.clear()
        pcm += bytes(640 - len(pcm))
        frame = av.AudioFrame(format='s16', layout='mono', samples=320)
        frame.sample_rate = 16000
        frame.pts = self.samples
        frame.time_base = Fraction(1, 16000)
        frame.planes[0].update(pcm)
        self.samples += 320
        return frame


class RealtimePeer:
    def __init__(self, events: asyncio.Queue, *, voice_epoch=0, realtime=False):
        self.events = events
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.track = RobotAudioTrack()
        self.pc.addTrack(self.track)
        self.channel = self.pc.createDataChannel('oai-events')
        self.connected = asyncio.get_running_loop().create_future()
        self.channel_ready = asyncio.get_running_loop().create_future()
        self.session_updated = asyncio.get_running_loop().create_future()
        self.configuration_sent = False
        self.receivers: set[asyncio.Task] = set()
        self.closing = False
        self.voice_epoch = voice_epoch
        self.realtime = realtime
        self.instructions = REALTIME_INSTRUCTIONS if realtime else VOICE_INSTRUCTIONS
        self.output_enabled = realtime
        self.started_at = asyncio.get_running_loop().time()
        self._diagnostics = dict(voiceEpoch=voice_epoch, dataEvents=[], speakable=[],
            instructionsSha256=hashlib.sha256(self.instructions.encode()).hexdigest(),
            receivedFrames=0, receivedBytes=0, silentFrames=0,
            outputDisabledFrames=0, admittedBytes=0, peak=0,
            sourceSampleRate=0, outputSampleRate=48000 if realtime else 24000,
            sampleRateConversions=0)

        @self.channel.on('open')
        def channel_opened():
            if not self.channel_ready.done():
                self.channel_ready.set_result(None)

        @self.channel.on('message')
        def message_received(message):
            try:
                if not isinstance(message, str) or len(message) > 65536:
                    raise ValueError('Invalid realtime data channel message')
                payload = json.loads(message)
                if not isinstance(payload, dict):
                    raise ValueError('Expected realtime event object')
                kind = payload.get('type')
                if isinstance(kind, str):
                    self._diagnostics['dataEvents'].append(dict(type=kind[:96],
                        seconds=round(asyncio.get_running_loop().time() - self.started_at, 3)))
                    self._diagnostics['dataEvents'] = self._diagnostics['dataEvents'][-24:]
                if payload.get('type') == 'session.updated' and self.configuration_sent and not self.session_updated.done():
                    self.session_updated.set_result(None)
                event = normalize_data_event(payload)
                if event:
                    self._emit(event)
            except (ValueError, TypeError, AttributeError) as error:
                self._emit({'method': 'local/error', 'params': {'message': error_message(error)}})

        @self.pc.on('connectionstatechange')
        async def state_changed():
            if self.pc.connectionState == 'connected' and not self.connected.done():
                self.connected.set_result(None)
            elif self.pc.connectionState == 'failed' and not self.closing:
                error = RuntimeError('Codex WebRTC media connection failed')
                if not self.connected.done():
                    self.connected.set_exception(error)
                self._emit({'method': 'local/error', 'params': {'message': error_message(error)}})

        @self.pc.on('track')
        def received(track):
            if track.kind == 'audio':
                task = asyncio.create_task(self._receive(track))
                self.receivers.add(task)
                task.add_done_callback(self.receivers.discard)

    def _emit(self, event):
        if not self.output_enabled and (event['method'] == 'thread/realtime/outputAudio/delta'
                or event.get('params', {}).get('role') == 'assistant'):
            return
        event = {**event, 'params': {**event.get('params', {}), 'voiceEpoch': self.voice_epoch}}
        if self.events.full():
            # A fatal event cannot depend on the ordinary queue having room.
            while not self.events.empty():
                self.events.get_nowait()
            event = {'method': 'local/error', 'params': {'message': 'Codex audio consumer is too slow'}}
        self.events.put_nowait(event)

    def diagnostics(self):
        """Bounded metadata only: no transcript, PCM, image, SDP or credentials."""
        return {**copy.deepcopy(self._diagnostics), 'upstream': self.track.diagnostics()}

    async def offer(self) -> str:
        await self.pc.setLocalDescription(await self.pc.createOffer())
        return self.pc.localDescription.sdp

    async def answer(self, sdp: str) -> None:
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type='answer'))
        await asyncio.wait_for(self.connected, 20)
        await asyncio.wait_for(self.channel_ready, 20)
        # Control travels over the same negotiated RTC data channel. This avoids
        # requiring a second sideband WebSocket to be reachable on the host.
        self.configuration_sent = True
        self.channel.send(json.dumps({'type': 'session.update', 'session': {
            'instructions': self.instructions,
            'delegation': {'type': 'client', 'ack_filler': self.realtime}}}))
        await asyncio.wait_for(asyncio.shield(self.session_updated), 10)

    async def speak(self, text):
        await asyncio.wait_for(asyncio.shield(self.session_updated), 10)
        if self.channel.readyState != 'open':
            raise ConnectionError('Codex RTC data channel is closed')
        self.output_enabled = True
        chunk = ''
        for character in text[:4000]:
            if len((chunk + character).encode()) > 500:
                self._speak_chunk(chunk)
                chunk = ''
            chunk += character
        if chunk:
            self._speak_chunk(chunk)

    def _speak_chunk(self, text):
        self.channel.send(json.dumps({'type': 'session.context.append', 'channel': 'speakable',
                                     'content': [{'type': 'input_text', 'text': text}]}))
        self._diagnostics['speakable'].append(dict(bytes=len(text.encode()),
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            seconds=round(asyncio.get_running_loop().time() - self.started_at, 3)))
        self._diagnostics['speakable'] = self._diagnostics['speakable'][-8:]

    async def context(self, text, *, delegation_id=None, channel='commentary'):
        """Return client-owned results to the same ongoing voice conversation."""
        if channel not in {'commentary', 'speakable', None}:
            raise ValueError('Unsupported realtime context channel')
        await asyncio.wait_for(asyncio.shield(self.session_updated), 10)
        if self.channel.readyState != 'open':
            raise ConnectionError('Codex RTC data channel is closed')
        chunk = ''
        for character in text[:4000]:
            if len((chunk + character).encode()) > 500:
                self._context_chunk(chunk, delegation_id, channel)
                chunk = ''
            chunk += character
        if chunk:
            self._context_chunk(chunk, delegation_id, channel)

    def _context_chunk(self, text, delegation_id, channel):
        payload = {'type': 'delegation.context.append' if delegation_id else 'session.context.append',
                   'content': [{'type': 'input_text', 'text': text}]}
        if delegation_id:
            payload['delegation_item_id'] = delegation_id
        if channel:
            payload['channel'] = channel
        self.channel.send(json.dumps(payload))

    async def _receive(self, track) -> None:
        rate = 48000 if self.realtime else 24000
        # Normalize channels without AV's +3 dB correlated-stereo downmix.
        normalizer = av.AudioResampler(format='s16')
        resampler = av.AudioResampler(format='s16', layout='mono', rate=rate)
        tail_samples = 0
        source_format = None
        try:
            while True:
                source = await track.recv()
                self._diagnostics['sourceSampleRate'] = source.sample_rate
                self._diagnostics['sampleRateConversions'] += source.sample_rate != rate
                if self.realtime:
                    current_format = (source.sample_rate, source.format.name, source.layout.name)
                    if source_format is not None and current_format != source_format:
                        raise ValueError('Native RTC source format changed during an audio track')
                    if source.format.name not in {'s16', 's16p', 'flt', 'fltp'}:
                        raise ValueError('Unsupported native RTC sample format')
                    if len(source.layout.channels) not in (1, 2):
                        raise ValueError('Unsupported native RTC channel layout')
                    if source.format.name in {'flt', 'fltp'}:
                        count = source.samples * (1 if source.format.is_planar else len(source.layout.channels))
                        for plane in source.planes:
                            if any(not math.isfinite(v) or abs(v) > 1 for (v,) in
                                   struct.iter_unpack('=f', bytes(plane)[:count * 4])):
                                raise ValueError('Invalid native RTC floating point audio')
                    source_format = current_format
                    if len(source.layout.channels) == 1 and source.format.name == 's16':
                        mono = source
                    else:
                        normalized = normalizer.resample(source)
                        if len(normalized) != 1 or normalized[0].samples != source.samples:
                            raise ValueError('Unexpected native RTC format normalization')
                        packed = normalized[0]
                        if len(packed.layout.channels) == 1:
                            mono = packed
                        else:
                            pairs = struct.iter_unpack('<hh', bytes(packed.planes[0])[:packed.samples * 4])
                            values = [round((left + right) / 2) for left, right in pairs]
                            mono = av.AudioFrame(format='s16', layout='mono', samples=source.samples)
                            mono.sample_rate, mono.pts = source.sample_rate, source.pts
                            if source.time_base is not None:
                                mono.time_base = source.time_base
                            mono.planes[0].update(struct.pack(f'<{len(values)}h', *values))
                    frames = [mono] if source.sample_rate == rate else resampler.resample(mono)
                else:
                    frames = resampler.resample(source)
                for frame in frames:
                    pcm = bytes(frame.planes[0])[:frame.samples * 2]
                    # RTP includes continuous silence. Only legacy utterance
                    # assembly suppresses idle frames; native retains the clock.
                    peak = max((abs(sample[0]) for sample in struct.iter_unpack('<h', pcm)), default=0)
                    self._diagnostics['receivedFrames'] += 1
                    self._diagnostics['receivedBytes'] += len(pcm)
                    self._diagnostics['peak'] = max(self._diagnostics['peak'], peak)
                    if peak > 64:
                        tail_samples = round(rate * .3)  # Keep a 300 ms acoustic tail.
                    elif tail_samples <= 0:
                        self._diagnostics['silentFrames'] += 1
                        # Native media must advance the DSP's real-time envelope
                        # even across long pauses. Legacy utterance assembly still
                        # suppresses idle silence.
                        if not self.realtime:
                            continue
                    else:
                        tail_samples -= frame.samples
                    if self.output_enabled:
                        self._diagnostics['admittedBytes'] += len(pcm)
                    else:
                        self._diagnostics['outputDisabledFrames'] += 1
                    self._emit({'method': 'thread/realtime/outputAudio/delta', 'params': {
                        'audio': {'data': base64.b64encode(pcm).decode(), 'sampleRate': rate,
                                  'numChannels': 1, 'samplesPerChannel': frame.samples}}})
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self.closing:
                if self.events.full():
                    self.events.get_nowait()
                self._emit({'method': 'local/error', 'params': {'message': error_message(error)}})

    async def close(self) -> None:
        self.closing = True
        self.track.stop()
        if not self.connected.done():
            self.connected.cancel()
        elif not self.connected.cancelled():
            self.connected.exception()
        if not self.channel_ready.done():
            self.channel_ready.cancel()
        if not self.session_updated.done():
            self.session_updated.cancel()
        tasks = list(self.receivers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.pc.close()
