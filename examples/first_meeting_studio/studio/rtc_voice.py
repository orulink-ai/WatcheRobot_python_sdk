"""Application-owned WebRTC playback; firmware derives mouths from speaker PCM."""
from __future__ import annotations

import asyncio
import logging
import time
from fractions import Fraction

from aiortc import AudioStreamTrack, RTCPeerConnection, RTCConfiguration, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp
from av import AudioFrame

logger = logging.getLogger(__name__)


class SpeechTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self._pcm = b''
        self._offset = 0
        self._done = None
        self._pts = 0
        self._origin = None
        self.late_frames = 0
        self.max_lateness_ms = 0.0

    def enqueue(self, pcm: bytes):
        if len(pcm) % 2:
            raise ValueError('PCM16 must contain whole samples')
        if self._done is not None and not self._done.done():
            raise RuntimeError('speech already playing')
        self._pcm, self._offset = pcm, 0
        self._done = asyncio.get_running_loop().create_future()
        return self._done

    async def recv(self):
        if self._origin is None:
            self._origin = time.monotonic()
        deadline = self._origin + self._pts / 24000
        now = time.monotonic()
        lateness = max(0, now - deadline)
        if lateness > .020:
            # Do not burst old packets after an event-loop stall. Preserve
            # every PCM sample and RTP timestamp, rebase only the wall clock.
            self._origin += lateness
            deadline = now
            self.late_frames += 1
            self.max_lateness_ms = max(self.max_lateness_ms, lateness * 1000)
        await asyncio.sleep(max(0, deadline - now))
        if self._offset >= len(self._pcm) and self._done is not None and not self._done.done():
            self._done.set_result(None)
        chunk = self._pcm[self._offset:self._offset + 960]
        self._offset += len(chunk)
        frame = AudioFrame(format='s16', layout='mono', samples=480)
        frame.planes[0].update(chunk.ljust(960, b'\x00'))
        frame.sample_rate, frame.pts, frame.time_base = 24000, self._pts, Fraction(1, 24000)
        self._pts += 480
        return frame


class RtcVoice:
    def __init__(self, rtc, stopped, playback_logger=None):
        self.rtc, self.stopped = rtc, stopped
        self.logger = playback_logger or logger
        self.peer = None
        self.track = None
        self.pump = None

    async def _events(self):
        cursor = 0
        pending = []
        while self.peer is not None:
            for event in self.rtc.events(after=cursor):
                cursor = event['id']
                message = event['message']
                data = message.get('data', {})
                if message.get('type') != 'evt.rtc.signal':
                    continue
                if data.get('kind') == 'answer':
                    await self.peer.setRemoteDescription(RTCSessionDescription(sdp=data['sdp'], type='answer'))
                    for candidate in pending:
                        await self.peer.addIceCandidate(candidate)
                    pending.clear()
                elif data.get('kind') == 'candidate' and data.get('candidate'):
                    candidate = candidate_from_sdp(data['candidate'].removeprefix('candidate:'))
                    candidate.sdpMid = data.get('sdp_mid', '0')
                    candidate.sdpMLineIndex = data.get('sdp_mline_index', 0)
                    if self.peer.remoteDescription:
                        await self.peer.addIceCandidate(candidate)
                    else:
                        pending.append(candidate)
            await asyncio.sleep(0.02)

    async def play(self, pcm: bytes):
        try:
            await asyncio.to_thread(self.rtc.start, mode='audio')
            self.peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            self.track = SpeechTrack()
            self.peer.addTrack(self.track)
            offer = await self.peer.createOffer()
            await self.peer.setLocalDescription(offer)
            self.pump = asyncio.create_task(self._events())
            await asyncio.to_thread(self.rtc.send_offer, self.peer.localDescription.sdp)
            deadline = time.monotonic() + 15
            while self.peer.connectionState != 'connected':
                self._check()
                if time.monotonic() >= deadline or self.peer.connectionState == 'failed':
                    raise TimeoutError('RTC 语音连接失败，请检查设备与电脑网络')
                await asyncio.sleep(0.05)
            done = self.track.enqueue(pcm)
            deadline = time.monotonic() + len(pcm) / 48000 + 10
            while not done.done():
                self._check()
                if time.monotonic() >= deadline:
                    raise TimeoutError('RTC 语音播放超时')
                await asyncio.sleep(0.02)
            # Allow the bounded receiver jitter queue to render the final PCM.
            for _ in range(20):
                self._check()
                await asyncio.sleep(0.02)
        finally:
            if self.track is not None:
                self.logger.info(
                    'RTC speech pacing: pcm_bytes=%d sent_bytes=%d late_frames=%d max_lateness_ms=%.1f',
                    len(pcm), self.track._offset, self.track.late_frames,
                    self.track.max_lateness_ms,
                )
            await self.close()

    def _check(self):
        if self.stopped.is_set():
            raise InterruptedError('已停止')
        if self.pump and self.pump.done():
            self.pump.result()
        if self.peer and self.peer.connectionState in {'failed', 'closed'}:
            raise RuntimeError('RTC 语音连接中断')

    async def close(self):
        if self.pump:
            self.pump.cancel()
            await asyncio.gather(self.pump, return_exceptions=True)
            self.pump = None
        if self.track:
            self.track.stop()
            self.track = None
        if self.peer:
            await self.peer.close()
            self.peer = None
        if self.rtc.snapshot().get('active'):
            await asyncio.to_thread(self.rtc.stop)
