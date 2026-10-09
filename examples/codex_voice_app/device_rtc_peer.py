"""Full-duplex device media, signalled only through public ApplicationRtc.

One instance owns one SDK session. It never opens a host microphone or a second
business connection, and never calls the legacy robot audio/microphone APIs.
PCM delivery is not an acoustic-quality or per-utterance playback receipt.
"""
from __future__ import annotations

import asyncio
import base64
from collections import deque
import copy
from fractions import Fraction
import math
import struct
import time

import av
from aiortc import (
    MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCRtpSender,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import candidate_from_sdp

from async_utils import drain_owned
from media_pacing import MediaPacer
from playout_buffer import PlayoutBuffer
from playback_volume import PlaybackVolume


SIGNAL_POLL_SECONDS = .05
HEARTBEAT_SECONDS = 1.0  # Firmware expires the control lease after five seconds.
CONNECT_TIMEOUT_SECONDS = 30.0
PEER_CLOSE_TIMEOUT_SECONDS = 5.0
STOP_CONFIRM_TIMEOUT_SECONDS = 5.0
SOURCE_STALL_SECONDS = 3.0
SOURCE_WATCH_INTERVAL_SECONDS = .1
MAX_PENDING_CANDIDATES = 32
MAX_EVENT_BACKLOG = 256
MAX_DIAGNOSTIC_INTEGER = 9_007_199_254_740_991
TX_PEAK_LIMIT = 29000
_AEC_STATS = (
    'audio_aec_reference_bytes',
    'audio_aec_reference_processed_bytes', 'audio_aec_reference_drops',
    'audio_aec_chunks', 'audio_aec_bypass_chunks',
    'audio_microphone_read_ewma_us', 'audio_microphone_read_max_us',
    'audio_aec_process_ewma_us', 'audio_aec_process_max_us', 'audio_aec_process_samples',
    'audio_opus_encode_ewma_us', 'audio_opus_encode_max_us', 'audio_opus_encode_samples',
)
_MEDIA_STATS = (
    'audio_capture_frames', 'audio_capture_bytes', 'audio_tx_packets', 'audio_tx_bytes', 'audio_tx_errors',
    'audio_tx_dropped_frames', 'audio_tx_dropped_bytes', 'audio_packets',
    'audio_decoded_frames', 'audio_i2s_bytes', 'audio_render_errors',
    'audio_queue_ms', 'audio_queue_dropped',
    'audio_pipeline_age_ewma_us', 'audio_pipeline_age_max_us',
    'audio_capture_peak', 'audio_microphone_peak', 'audio_pcm_peak',
    'audio_pcm_rms', 'audio_raw_rms', 'audio_raw_peak', 'audio_output_rms',
    'audio_output_peak', 'audio_limiter_hits',
)
_SIGNED_MEDIA_STATS = ('audio_tx_last_error', 'audio_gain_db_x100')


def _numeric_stats(data, keys, *, signed=False):
    lower = -MAX_DIAGNOSTIC_INTEGER if signed else 0
    return {key: data[key] for key in keys
            if type(data.get(key)) is int and lower <= data[key] <= MAX_DIAGNOSTIC_INTEGER}


class PcmEnergy:
    """Constant-space s16le amplitude summaries, never samples or a waveform."""

    def __init__(self):
        self.samples = self.squares = self.peak = self.clipped_samples = 0
        self.silent_frames = self.last_peak = self.last_energy = self.last_samples = 0

    def add(self, pcm: bytes) -> None:
        energy = peak = clipped = 0
        for (sample,) in struct.iter_unpack('<h', pcm):
            magnitude = abs(sample)
            peak = max(peak, magnitude)
            energy += sample * sample
            clipped += magnitude >= 32767
        count = len(pcm) // 2
        self.last_samples, self.last_energy, self.last_peak = count, energy, peak
        self.samples += count
        self.squares += energy
        self.peak = max(self.peak, peak)
        self.clipped_samples += clipped
        self.silent_frames += bool(count and peak == 0)

    def snapshot(self):
        mean_square = self.last_energy / self.last_samples if self.last_samples else 0
        rms = math.sqrt(mean_square)
        return dict(samples=self.samples, peak=self.peak, lastPeak=self.last_peak,
                    lastEnergy=self.last_energy, lastMeanSquare=mean_square,
                    lastRms=round(rms, 3),
                    rms=round(math.sqrt(self.squares / self.samples), 3) if self.samples else 0,
                    lastRmsDbfs=round(20 * math.log10(rms / 32768), 3) if rms else None,
                    silentFrames=self.silent_frames, clippedSamples=self.clipped_samples)


class DeviceAudioTrack(MediaStreamTrack):
    """Paced 48 kHz mono TX, native passthrough or legacy persistent SRC."""

    kind = 'audio'

    def __init__(self, *, clock=time.perf_counter, sleep=None, on_failure=None):
        super().__init__()
        self.pacing = MediaPacer(clock=clock, sleep=sleep)
        self.playout = PlayoutBuffer(clock, on_failure)
        self.buffer = self.playout.buffer
        # Integer resampling can saturate before any later limiter sees the
        # intersample overshoot. Keep packed float until frame-wide attenuation.
        self.resampler = av.AudioResampler(format='flt', layout='mono', rate=48000)
        self.input_samples = self.samples = 0
        self.input_sample_rate = None
        self.underflow_frames = 0
        self.partial_underflow_frames = self.empty_underflow_frames = 0
        self.input_energy, self.output_energy = PcmEnergy(), PcmEnergy()
        self.playback_volume = PlaybackVolume()
        self.peak_limiter = dict(peakLimit=TX_PEAK_LIMIT, resampledFrames=0,
                                passthroughFrames=0, inputSampleRate=None,
                                idleSkippedFrames=0, idleSkippedSamples=0,
                                limitedFrames=0, preLimitPeak=0.0, preLimitPeakMax=0.0,
                                lastScale=1.0, minScale=1.0)

    def configure_playout(self, *, prefill_ms, max_buffer_ms):
        self.playout.configure(prefill_ms=prefill_ms, max_buffer_ms=max_buffer_ms)

    def playout_diagnostics(self):
        return self.playout.diagnostics()

    def push(self, pcm: bytes, *, sample_rate=24000, received_at=None) -> None:
        if self.readyState != 'live':
            raise RuntimeError('Device RTC audio track is stopped')
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
            raise ValueError('Expected non-empty mono s16le PCM bytes')
        if type(sample_rate) is not int or sample_rate not in (24000, 48000):
            raise ValueError('Expected 24 or 48 kHz input')
        if self.input_sample_rate is not None and sample_rate != self.input_sample_rate:
            raise ValueError('Input sample rate cannot change during a media session')
        self.playout.validate_age(received_at)
        # Check before allocation/resampler mutation; include its small delayed tail.
        predicted = len(pcm) * (48000 // sample_rate) + (128 if sample_rate != 48000 else 0)
        self.playout.validate_growth(predicted)
        self.input_sample_rate = sample_rate
        self.peak_limiter['inputSampleRate'] = sample_rate
        if sample_rate == 48000:
            values = [item[0] for item in struct.iter_unpack('<h', pcm)]
            self._queue_samples(values, passthrough=True, received_at=received_at)
            self.input_samples += len(values)
            self.input_energy.add(pcm)
            return
        frame = av.AudioFrame(format='s16', layout='mono', samples=len(pcm) // 2)
        frame.sample_rate = sample_rate
        frame.pts = self.input_samples
        frame.time_base = Fraction(1, sample_rate)
        frame.planes[0].update(pcm)
        frames = self.resampler.resample(frame)
        self.input_samples += frame.samples
        for output in frames:
            if not output.samples:
                continue
            # AV packed float samples use native endian; the wire stays s16le.
            values = [value * 32768 for (value,) in struct.iter_unpack(
                '=f', bytes(output.planes[0])[:output.samples * 4])]
            if not all(math.isfinite(value) for value in values):
                raise ValueError('Non-finite device RTC resampled audio')
            self._queue_samples(values, received_at=received_at)
        self.input_energy.add(pcm)

    def _queue_samples(self, values, *, passthrough=False, received_at=None):
        peak = max(map(abs, values))
        limiter = self.peak_limiter
        limiter['passthroughFrames' if passthrough else 'resampledFrames'] += 1
        if passthrough and peak == 0 and not self.buffer:
            # recv() supplies continuous paced idle zeros. Startup-silence
            # bursts must not build a second FIFO. If program audio is queued,
            # retain zeros in order so word pauses and tails are not shortened.
            limiter['idleSkippedFrames'] += 1
            limiter['idleSkippedSamples'] += len(values)
            return
        scale = min(1.0, TX_PEAK_LIMIT / peak) if peak else 1.0
        # Last-resort protection. Native DSP already bounds peaks: scale must
        # stay unity there, so this is not a competing dynamic gain stage.
        self.playout.append(struct.pack(f'<{len(values)}h', *(round(value * scale) for value in values)),
                            received_at=received_at)
        limiter['limitedFrames'] += scale < 1.0
        limiter.update(preLimitPeak=peak,
                       preLimitPeakMax=max(limiter['preLimitPeakMax'], peak),
                       lastScale=scale, minScale=min(limiter['minScale'], scale))

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError
        await self.pacing.wait()
        if self.readyState != 'live':
            raise MediaStreamError
        pcm = self.playout.take(1920)
        if len(pcm) < 1920:
            if not self.playout.waiting:
                self.underflow_frames += 1
                self.partial_underflow_frames += bool(pcm)
                self.empty_underflow_frames += not pcm
            pcm += bytes(1920 - len(pcm))
        # Last stage, including queued audio and tails. AGC cannot undo a
        # quieter debug level, and changing it adds no second media queue.
        pcm = self.playback_volume.process(pcm)
        self.output_energy.add(pcm)
        frame = av.AudioFrame(format='s16', layout='mono', samples=960)
        frame.sample_rate = 48000
        frame.pts = self.samples
        frame.time_base = Fraction(1, 48000)
        frame.planes[0].update(pcm)
        self.samples += 960
        return frame

    def stop(self):
        self.playout.clear()
        super().stop()


class DeviceRtcPeer:
    def __init__(self, rtc, events: asyncio.Queue):
        self.rtc, self.events = rtc, events
        self.pc = None
        self.track = DeviceAudioTrack(on_failure=self._fail)
        self._connected = None
        self._start_task = self._close_task = None
        self._workers: set[asyncio.Task] = set()
        self._sdk_owned = False
        self._device_stopped = False
        self._closing = self._closed = self._failed = False
        self._cursor = 0
        self._candidates = []
        self._event_types = deque(maxlen=24)
        self._stats = {}
        self._media_stats = {}
        self._received_frames = self._received_bytes = 0
        self._queued_bytes = 0
        self._received_energy = PcmEnergy()
        self._stats_at = self._last_source_at = self._last_source_pts = None
        self._last_source_duration = 0
        self._clock_anchor = None
        self._source_connected_at = None
        self._source_stalled = False
        self._receive_clock = dict(sourceFrames=0, sourceRate=0, sourceChannels=0,
                                  sourceDurationMs=0, missingPtsFrames=0, ptsRegressions=0,
                                  rateChanges=0, ptsDiscontinuities=0,
                                  arrivalIntervalMs=0, arrivalIntervalMaxMs=0,
                                  arrivalMediaSkewMs=None, arrivalMediaSkewMinMs=None,
                                  arrivalMediaSkewMaxMs=None)
        self._event_timing = dict(queueDepthMax=0, receiveToEnqueueMs=0,
                                  receiveToEnqueueMaxMs=0, lastEnqueuedAt=None)

    def _now(self):
        return time.monotonic()

    def _note_source_frame(self, frame, received_at):
        """PTS vs host arrival is relative skew, not absolute capture latency.

        recv() has already dequeued aiortc's frame. We neither inspect its
        private jitter queue nor re-clock, trim, buffer or drop media here.
        """
        clock = self._receive_clock
        rate, samples = frame.sample_rate, frame.samples
        duration = samples / rate if rate else 0
        pts = float(frame.pts * frame.time_base) if frame.pts is not None and frame.time_base else None
        changed = bool(clock['sourceRate'] and clock['sourceRate'] != rate)
        regressed = pts is not None and self._last_source_pts is not None and pts < self._last_source_pts
        clock['sourceFrames'] += 1
        clock['rateChanges'] += changed
        clock['ptsRegressions'] += regressed
        clock.update(sourceRate=rate, sourceChannels=len(frame.layout.channels),
                     sourceDurationMs=duration * 1000)
        if self._last_source_at is not None:
            interval = max(0, (received_at - self._last_source_at) * 1000)
            clock['arrivalIntervalMs'] = interval
            clock['arrivalIntervalMaxMs'] = max(clock['arrivalIntervalMaxMs'], interval)
        if pts is None:
            clock['missingPtsFrames'] += 1
            clock['arrivalMediaSkewMs'] = None
            self._clock_anchor = None
        else:
            if self._last_source_pts is not None and not changed:
                expected = self._last_source_pts + self._last_source_duration
                clock['ptsDiscontinuities'] += abs(pts - expected) > (1 / rate if rate else 0)
            if self._clock_anchor is None or changed or regressed:
                self._clock_anchor = received_at, pts
            anchor_at, anchor_pts = self._clock_anchor
            skew = ((received_at - anchor_at) - (pts - anchor_pts)) * 1000
            clock['arrivalMediaSkewMs'] = skew
            for key, select in [('arrivalMediaSkewMinMs', min), ('arrivalMediaSkewMaxMs', max)]:
                clock[key] = skew if clock[key] is None else select(clock[key], skew)
        self._last_source_at, self._last_source_pts = received_at, pts
        self._last_source_duration = duration
        return dict(clock='host-monotonic', sourceReceivedAt=received_at,
                    sourceFrameSequence=clock['sourceFrames'], sourcePtsSeconds=pts,
                    sourceSampleRate=rate, sourceSamples=samples, sourceDurationMs=duration * 1000)

    async def _sdk_call(self, callback, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Thread cancellation is not an SDK sending fence, even on a second cancel.
            await drain_owned(task)
            raise

    def _live(self):
        if self._closing:
            raise asyncio.CancelledError()
        if self._failed:
            raise RuntimeError('Device RTC session failed')

    async def start(self):
        if self._closing or self._failed:
            raise RuntimeError('Device RTC instances cannot be restarted')
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start())
        try:
            await asyncio.shield(self._start_task)
        except BaseException:
            await self.close()
            raise

    async def _start(self):
        snapshot = await self._sdk_call(self.rtc.snapshot)
        self._live()
        if snapshot.get('active'):
            raise RuntimeError('An SDK RTC session is already active')
        self._sdk_owned = True  # A lost start ACK is not proof that no lease exists.
        await self._sdk_call(self.rtc.start, mode='audio')
        self._live()
        self._connected = asyncio.get_running_loop().create_future()
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        transceiver = self.pc.addTransceiver(self.track, direction='sendrecv')
        codecs = [codec for codec in RTCRtpSender.getCapabilities('audio').codecs
                  if codec.mimeType.lower() == 'audio/opus' and codec.clockRate == 48000]
        if not codecs:
            raise RuntimeError('Host Opus codec is unavailable')
        # Opus SDP conventionally advertises /2; PCM tracks remain mono. Do not
        # invent a non-capability /1 codec or allow PCMU to win negotiation.
        transceiver.setCodecPreferences(codecs)

        @self.pc.on('connectionstatechange')
        async def state_changed():
            if self._closing:
                return
            state = self.pc.connectionState
            if state == 'connected':
                if self._source_connected_at is None:
                    self._source_connected_at = self._now()
                if not self._connected.done():
                    self._connected.set_result(None)
            elif state in {'failed', 'closed', 'disconnected'}:
                self._fail('Device RTC media connection failed')

        @self.pc.on('track')
        def received(track):
            if track.kind == 'audio' and not self._closing and not self._failed:
                self._spawn(lambda: self._receive(track), 'Device RTC microphone receiver failed')

        self._spawn(self._poll, 'Device RTC signalling failed')
        self._spawn(self._heartbeat, 'Device RTC heartbeat failed')
        self._spawn(self._watch_source, 'Device RTC source watchdog failed')
        offer = await self.pc.createOffer()
        self._live()
        await self.pc.setLocalDescription(offer)
        self._live()
        await self._sdk_call(self.rtc.send_offer, self.pc.localDescription.sdp)
        self._live()
        await asyncio.wait_for(asyncio.shield(self._connected), CONNECT_TIMEOUT_SECONDS)
        self._live()

    def _spawn(self, callback, failure_message):
        async def run():
            try:
                await callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._closing:
                    self._fail(failure_message)
        task = asyncio.create_task(run())
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)

    def _fail(self, message):
        if self._failed or self._closing:
            return
        self._failed = True
        self.track.stop()
        if self._connected is not None and not self._connected.done():
            self._connected.set_exception(RuntimeError(message))
            self._connected.exception()  # Also observe failures before start reaches its wait.
        while not self.events.empty():
            self.events.get_nowait()
        self.events.put_nowait({'method': 'local/error', 'params': {'message': message}})

    async def _heartbeat(self):
        while not self._closing and not self._failed:
            await self._sdk_call(self.rtc.clock_ping,
                                 max(1, int(asyncio.get_running_loop().time() * 1_000_000)))
            await asyncio.sleep(HEARTBEAT_SECONDS)

    def _source_gap(self, now):
        if (self._source_connected_at is None or self.pc is None
                or self.pc.connectionState != 'connected'):
            return None
        reference = self._source_connected_at
        if self._last_source_at is not None:
            reference = max(reference, self._last_source_at)
        return max(0, now - reference)

    def _check_source(self, now):
        if self._closing or self._failed:
            return
        gap = self._source_gap(now)
        if gap is not None and gap > SOURCE_STALL_SECONDS:
            self._source_stalled = True
            self._fail('Device RTC microphone source stalled for more than three seconds')

    async def _watch_source(self):
        # Continuous decoded device capture includes silence. Only source frames
        # renew this clock; SDK heartbeats, stats and downlink do not. The owner
        # handles the fatal event and closes the retained SDK lease normally.
        while not self._closing and not self._failed:
            await asyncio.sleep(SOURCE_WATCH_INTERVAL_SECONDS)
            self._check_source(self._now())

    async def _poll(self):
        while not self._closing and not self._failed:
            items = await self._sdk_call(self.rtc.events, after=self._cursor)
            self._live()
            for item in items:
                sequence = item['id']
                if sequence != self._cursor + 1:
                    raise RuntimeError('Device RTC event history overflow')
                self._cursor = sequence
                message = item['message']
                kind, data = message.get('type'), message.get('data', {})
                self._event_types.append(kind if kind in {
                    'evt.rtc.signal', 'evt.rtc.state', 'evt.rtc.stats',
                    'evt.rtc.capabilities', 'sys.ack', 'sys.nack',
                } else 'other')
                if kind == 'evt.rtc.signal':
                    await self._signal(data)
                elif kind in {'sys.nack', 'evt.rtc.error'} or (
                        kind == 'evt.rtc.state' and data.get('state') in {'failed', 'stopped'}):
                    self._fail('Device RTC session failed or stopped')
                elif kind == 'evt.rtc.stats':
                    self._stats_at = self._now()
                    self._stats = _numeric_stats(data, _AEC_STATS)
                    if type(data.get('audio_aec_active')) is bool:
                        self._stats['audio_aec_active'] = data['audio_aec_active']
                    self._media_stats = {**_numeric_stats(data, _MEDIA_STATS),
                                         **_numeric_stats(data, _SIGNED_MEDIA_STATS, signed=True)}
                if self._closing or self._failed:
                    return
            await asyncio.sleep(SIGNAL_POLL_SECONDS)

    async def _signal(self, data):
        if data.get('kind') == 'answer':
            sdp = data.get('sdp')
            if not isinstance(sdp, str) or not sdp or len(sdp.encode()) > 16384:
                raise ValueError('Invalid device RTC answer')
            if self.pc.remoteDescription is not None:
                return
            await self.pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type='answer'))
            self._live()
            for candidate in self._candidates:
                await self.pc.addIceCandidate(candidate)
                self._live()
            self._candidates.clear()
        elif data.get('kind') == 'candidate':
            value = data.get('candidate')
            if not isinstance(value, str) or len(value.encode()) > 2048:
                raise ValueError('Invalid device RTC candidate')
            candidate = candidate_from_sdp(value.removeprefix('candidate:')) if value else None
            if candidate is not None:
                candidate.sdpMid = data.get('sdp_mid')
                candidate.sdpMLineIndex = data.get('sdp_mline_index')
            if self.pc.remoteDescription is not None:
                await self.pc.addIceCandidate(candidate)
            else:
                if len(self._candidates) >= MAX_PENDING_CANDIDATES:
                    raise RuntimeError('Too many pending device RTC candidates')
                self._candidates.append(candidate)

    async def _receive(self, track):
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        while not self._closing and not self._failed:
            source = await track.recv()
            received_at = self._now()
            if self._closing or self._failed:
                return
            source_timing = self._note_source_frame(source, received_at)
            frames = resampler.resample(source)
            for frame in frames:
                pcm = bytes(frame.planes[0])[:frame.samples * 2]
                if not pcm:
                    continue
                if self.events.full() or self.events.qsize() >= MAX_EVENT_BACKLOG:
                    self._fail('Device RTC audio consumer is too slow')
                    return
                self._received_frames += 1
                self._received_bytes += len(pcm)
                self._received_energy.add(pcm)
                encoded = base64.b64encode(pcm).decode('ascii')
                enqueued_at, depth = self._now(), self.events.qsize() + 1
                processing_ms = max(0, (enqueued_at - received_at) * 1000)
                self._event_timing.update(
                    queueDepthMax=max(self._event_timing['queueDepthMax'], depth),
                    receiveToEnqueueMs=processing_ms,
                    receiveToEnqueueMaxMs=max(self._event_timing['receiveToEnqueueMaxMs'], processing_ms),
                    lastEnqueuedAt=enqueued_at)
                # Consumer measures exact residence using time.monotonic() -
                # enqueuedAt; qsize is only depth, never an inferred duration.
                timing = {**source_timing, 'enqueuedAt': enqueued_at,
                          'processingMs': processing_ms, 'queueDepthAtEnqueue': depth}
                self.events.put_nowait({'method': 'local/deviceAudio', 'params': {'audio': {
                    'data': encoded, 'sampleRate': 16000,
                    'numChannels': 1, 'samplesPerChannel': frame.samples}, 'timing': timing}})

    async def append_audio(self, pcm: bytes, *, sample_rate=24000, received_at=None):
        if (self._closing or self._failed or self.pc is None
                or self.pc.connectionState != 'connected'):
            raise RuntimeError('Device RTC media is not connected')
        self.track.push(pcm, sample_rate=sample_rate, received_at=received_at)
        self._queued_bytes += len(pcm)

    async def close(self):
        self._closing = True
        retry = (self._close_task is not None and self._close_task.done()
                 and not self._close_task.cancelled() and self._close_task.exception() is not None)
        if self._close_task is None or retry:
            self._close_task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await drain_owned(self._close_task)
            raise

    async def _close(self):
        self.track.stop()
        workers = set(self._workers)
        if self._start_task is not None and not self._start_task.done():
            workers.add(self._start_task)
        for task in workers:
            task.cancel()
        # SDK calls inside these workers drain their threads before cancellation
        # returns, so no late start/offer/heartbeat can follow the final stop.
        await asyncio.gather(*workers, return_exceptions=True)
        self._workers.clear()
        if self._connected is not None and not self._connected.done():
            self._connected.cancel()
        self._candidates.clear()
        failures = []

        async def stop_sdk():
            if self._sdk_owned:
                await self._sdk_call(self.rtc.stop)
                # Firmware may ACK an already-stopping request before teardown
                # completes. ApplicationRtc's local "stopped" snapshot after ACK
                # is not a Device release receipt. Only the public, session-filtered
                # event emitted after actual teardown seals this ownership.
                async def wait_for_receipt():
                    while True:
                        items = await self._sdk_call(self.rtc.events, after=0)
                        if any(item.get('message', {}).get('type') == 'evt.rtc.state'
                               and item['message'].get('data', {}).get('state') == 'stopped'
                               for item in items):
                            self._device_stopped = True
                            return
                        await asyncio.sleep(SIGNAL_POLL_SECONDS)
                await asyncio.wait_for(wait_for_receipt(), STOP_CONFIRM_TIMEOUT_SECONDS)
                self._sdk_owned = False

        async def stop_peer():
            if self.pc is not None:
                await asyncio.wait_for(self.pc.close(), PEER_CLOSE_TIMEOUT_SECONDS)
                self.pc = None  # Retain failed peers for an actual close retry.

        results = await asyncio.gather(stop_sdk(), stop_peer(), return_exceptions=True)
        failures.extend(type(result).__name__ for result in results if isinstance(result, BaseException))
        if failures:
            raise RuntimeError('Device RTC cleanup failed: ' + ', '.join(failures))
        self._closed = True

    def diagnostics(self):
        """Bounded allowlisted metadata; never SDP, IDs, credentials or PCM."""
        now = self._now()
        source_gap = self._source_gap(now)
        return copy.deepcopy(dict(
            connected=bool(self.pc is not None and self.pc.connectionState == 'connected'
                           and not self._closing and not self._failed),
            failed=self._failed, closing=self._closing,
            stopConfirmed=self._closed and not self._sdk_owned and self.pc is None,
            deviceStoppedReceipt=self._device_stopped,
            receivedFrames=self._received_frames, receivedBytes=self._received_bytes,
            queuedInputBytes=self._queued_bytes, bufferedBytes=len(self.track.buffer),
            underflowFrames=self.track.underflow_frames,
            downlinkPacing=self.track.pacing.diagnostics(),
            downlinkPlayout=self.track.playout_diagnostics(),
            downlinkVolume=self.track.playback_volume.diagnostics(),
            partialUnderflowFrames=self.track.partial_underflow_frames,
            emptyUnderflowFrames=self.track.empty_underflow_frames,
            eventTypes=list(self._event_types), aec=self._stats, media=self._media_stats,
            deviceStatsAgeMs=max(0, (now - self._stats_at) * 1000) if self._stats_at is not None else None,
            receivedAudioEnergy=self._received_energy.snapshot(),
            downlinkInputEnergy=self.track.input_energy.snapshot(),
            downlinkTrackEnergy=self.track.output_energy.snapshot(),
            downlinkPeakLimiter=self.track.peak_limiter,
            receiveClock={**self._receive_clock,
                          'lastFrameAgoMs': max(0, (now - self._last_source_at) * 1000)
                          if self._last_source_at is not None else None},
            eventTiming={**self._event_timing, 'queueDepth': self.events.qsize()},
            sourceWatch=dict(timeoutSeconds=SOURCE_STALL_SECONDS, stalled=self._source_stalled,
                             armed=source_gap is not None and not self._closing and not self._failed,
                             gapMs=source_gap * 1000 if source_gap is not None else None),
        ))
