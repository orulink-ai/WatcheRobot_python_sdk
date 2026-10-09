"""Host-only RTC contract tests: no robot, cloud, host microphone or sockets."""
import asyncio
import base64
from fractions import Fraction
import importlib
import json
import math
from pathlib import Path
import sys
import threading
import struct
from types import SimpleNamespace

import av
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


class FakeRtc:
    def __init__(self):
        self.calls = []
        self.messages = []
        self.active = False
        self.state = 'idle'
        self.start_entered = threading.Event()
        self.start_release = threading.Event()
        self.start_release.set()
        self.stop_entered = threading.Event()
        self.stop_release = threading.Event()
        self.stop_release.set()
        self.stop_error = None
        self.stop_result = True

    def start(self, *, mode):
        self.calls.append(('start', mode))
        self.start_entered.set()
        assert self.start_release.wait(3)
        self.active, self.state = True, 'starting'
        return self.snapshot()

    def send_offer(self, sdp):
        self.calls.append(('offer', sdp))
        self.emit('evt.rtc.signal', kind='candidate',
                  candidate='candidate:1 1 UDP 2130706431 127.0.0.1 12345 typ host',
                  sdp_mid='0', sdp_mline_index=0)
        self.emit('evt.rtc.signal', kind='answer', sdp='device-answer')

    def emit(self, message_type, **data):
        self.messages.append({'id': len(self.messages) + 1,
                              'message': {'type': message_type, 'data': data}})

    def events(self, *, after):
        return [item for item in list(self.messages) if item['id'] > after]

    def clock_ping(self, timestamp):
        self.calls.append(('ping', timestamp))

    def snapshot(self):
        return {'active': self.active, 'state': self.state}

    def stop(self):
        self.calls.append(('stop', None))
        self.stop_entered.set()
        assert self.stop_release.wait(3)
        if self.stop_error:
            raise self.stop_error
        if self.stop_result:
            self.active, self.state = False, 'stopped'
            self.emit('evt.rtc.state', state='stopped')
        return self.stop_result


class FakePeer:
    def __init__(self, configuration):
        assert configuration.iceServers == []
        self.callbacks = {}
        self.connectionState = 'new'
        self.localDescription = self.remoteDescription = None
        self.preferences = []
        self.candidates = []
        self.connect_on_answer = True
        self.close_calls = 0
        self.close_error = None

    def on(self, event):
        def register(callback):
            self.callbacks[event] = callback
            return callback
        return register

    def addTransceiver(self, track, *, direction):
        assert direction == 'sendrecv'
        self.track = track
        return SimpleNamespace(setCodecPreferences=self.preferences.extend)

    async def createOffer(self):
        return SimpleNamespace(type='offer', sdp='host-offer')

    async def setLocalDescription(self, description):
        self.localDescription = description

    async def setRemoteDescription(self, description):
        self.remoteDescription = description
        if self.connect_on_answer:
            await self.connect()

    async def connect(self):
        self.connectionState = 'connected'
        await self.callbacks['connectionstatechange']()

    async def addIceCandidate(self, candidate):
        assert self.remoteDescription is not None
        self.candidates.append(candidate)

    async def close(self):
        self.close_calls += 1
        if self.close_error:
            raise self.close_error
        self.connectionState = 'closed'


@pytest.fixture
def module(monkeypatch):
    result = importlib.import_module('device_rtc_peer')
    monkeypatch.setattr(result, 'RTCPeerConnection', FakePeer)
    monkeypatch.setattr(result, 'SIGNAL_POLL_SECONDS', .002)
    monkeypatch.setattr(result, 'HEARTBEAT_SECONDS', .01)
    monkeypatch.setattr(result, 'CONNECT_TIMEOUT_SECONDS', .2)
    monkeypatch.setattr(result, 'STOP_CONFIRM_TIMEOUT_SECONDS', .05, raising=False)
    monkeypatch.setattr(result, 'SOURCE_WATCH_INTERVAL_SECONDS', .002, raising=False)
    return result


async def wait_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(.002)
    await asyncio.wait_for(wait(), 2)


def test_start_negotiates_only_opus_and_waits_for_actual_connection(module, monkeypatch):
    async def run():
        peers = []
        def create(configuration):
            pc = FakePeer(configuration)
            pc.connect_on_answer = False
            peers.append(pc)
            return pc
        monkeypatch.setattr(module, 'RTCPeerConnection', create)
        rtc = FakeRtc()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        starting = asyncio.create_task(peer.start())
        await wait_until(lambda: peers and peers[0].remoteDescription is not None)
        assert not starting.done(), 'SDK ACK and answer alone are not connected'
        assert rtc.calls[0] == ('start', 'audio')
        assert peers[0].preferences
        assert all(c.mimeType.lower() == 'audio/opus' and c.clockRate == 48000
                   for c in peers[0].preferences)
        await peers[0].connect()
        await starting
        await wait_until(lambda: len(peers[0].candidates) == 1)
        assert peers[0].candidates[0].sdpMid == '0'
        await wait_until(lambda: any(kind == 'ping' for kind, _ in rtc.calls))
        assert any(kind == 'ping' and stamp > 0 for kind, stamp in rtc.calls)
        await peer.close()
        assert rtc.state == 'stopped' and peers[0].close_calls == 1
        await peer.close()
        assert len([c for c in rtc.calls if c[0] == 'stop']) == 1
    asyncio.run(run())


def test_receive_is_device_pcm_16k_mono_including_silence(module):
    async def run():
        events = asyncio.Queue(maxsize=8)
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        await peer.start()
        class Source:
            kind = 'audio'
            def __init__(self):
                self.index = 0
                self.drained = False
            async def recv(self):
                if self.index == 3:
                    try:
                        await asyncio.Future()
                    finally:
                        self.drained = True
                frame = av.AudioFrame(format='s16', layout='mono', samples=960)
                frame.sample_rate = 48000
                frame.pts = self.index * 960
                frame.time_base = Fraction(1, 48000)
                frame.planes[0].update(b'\x00\x00' * 960)
                self.index += 1
                return frame
        source = Source()
        peer.pc.callbacks['track'](source)
        event = await asyncio.wait_for(events.get(), 2)
        assert event['method'] == 'local/deviceAudio'
        audio = event['params']['audio']
        pcm = base64.b64decode(audio['data'], validate=True)
        assert audio['sampleRate'] == 16000 and audio['numChannels'] == 1
        assert pcm and set(pcm) == {0}
        assert len(pcm) == audio['samplesPerChannel'] * 2
        await wait_until(lambda: source.index == 3)
        await peer.close()
        assert source.drained
    asyncio.run(run())


def test_downlink_is_paced_resampled_bounded_and_underflow_is_zero(module):
    async def run():
        peer = module.DeviceRtcPeer(FakeRtc(), asyncio.Queue())
        with pytest.raises(RuntimeError):
            await peer.append_audio(b'\x01\x00' * 480)
        await peer.start()
        await peer.append_audio(b'\x00\x10' * 24000)
        first, second = await peer.track.recv(), await peer.track.recv()
        assert first.sample_rate == 48000 and first.layout.name == 'mono'
        assert first.samples == second.samples == 960
        assert first.pts == 0 and second.pts == 960
        with pytest.raises(ValueError):
            await peer.append_audio(b'x')
        with pytest.raises(RuntimeError, match='backlog'):
            await peer.append_audio(b'\x00\x10' * 48000)
        assert peer.diagnostics()['bufferedBytes'] <= 192000
        await peer.close()
        with pytest.raises(RuntimeError):
            await peer.append_audio(b'\x01\x00' * 480)
        idle = module.DeviceAudioTrack()
        frame = await idle.recv()
        assert bytes(frame.planes[0]) == bytes(1920)
        assert idle.underflow_frames == 1
        idle.stop()
    asyncio.run(run())


def test_live_overload_fences_track_and_notifies_service_before_teardown(module):
    async def run():
        events = asyncio.Queue()
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        peer.track.configure_playout(prefill_ms=80, max_buffer_ms=600)
        await peer.start()
        await peer.append_audio(b'\xe8\x03' * 28800, sample_rate=48000)
        with pytest.raises(RuntimeError, match='backlog'):
            await peer.append_audio(b'\xe8\x03' * 960, sample_rate=48000)
        assert peer.track.readyState == 'ended' and not peer.track.buffer
        assert peer.diagnostics()['failed']
        assert peer.track.playout_diagnostics()['discardedOnFailureMs'] == 600
        event = events.get_nowait()
        assert event['method'] == 'local/error' and 'backlog' in event['params']['message']
        with pytest.raises(RuntimeError):
            await peer.append_audio(b'\xe8\x03' * 960, sample_rate=48000)
        assert events.empty(), 'Failure must emit only once'
        await peer.close()
        assert peer.diagnostics()['deviceStoppedReceipt']
    asyncio.run(run())


@pytest.mark.parametrize('run_length', [2, 3, 4])
def test_downlink_gain_then_resample_normalizes_transients_before_s16_packing(module, run_length):
    from playback_gain import PlaybackGain

    async def run():
        gain = PlaybackGain()
        quiet = struct.pack('<h', 1000) * 480
        for _ in range(100):
            gain.process(quiet)
        values = [1000] * 480
        values[200:200 + 2 * run_length] = [-10000] * run_length + [10000] * run_length
        source = gain.process(struct.pack('<480h', *values))
        assert gain.diagnostics()['outputPeak'] <= 29000

        # Independent float reference proves the resampler's overshoot was not
        # already clipped, and the whole frame is scaled, not individual peaks.
        reference = av.AudioResampler(format='flt', layout='mono', rate=48000)
        frames = reference.resample(pcm_frame(
            [item[0] for item in struct.iter_unpack('<h', source)], rate=24000))
        raw = [value * 32768 for output in frames
               for (value,) in struct.iter_unpack('=f', bytes(output.planes[0])[:output.samples * 4])]
        peak = max(map(abs, raw))
        assert peak > 29000
        expected = struct.pack(f'<{len(raw)}h', *(round(value * 29000 / peak) for value in raw))

        peer = module.DeviceRtcPeer(FakeRtc(), asyncio.Queue())
        try:
            await peer.start()
            await peer.append_audio(source)
            frame = await peer.track.recv()
            output = bytes(frame.planes[0])
            assert max(abs(value) for (value,) in struct.iter_unpack('<h', output)) <= 29000
            assert output == expected + bytes(1920 - len(expected))
            diagnostics = peer.diagnostics()
            assert diagnostics['downlinkTrackEnergy']['clippedSamples'] == 0
            limiter = diagnostics['downlinkPeakLimiter']
            assert limiter['peakLimit'] == 29000 and limiter['limitedFrames'] == 1
            assert limiter['resampledFrames'] == 1 and limiter['preLimitPeakMax'] > 29000
            assert limiter['lastScale'] == pytest.approx(29000 / peak)
            json.dumps(diagnostics, allow_nan=False)
        finally:
            await peer.close()
    asyncio.run(run())


def test_downlink_float_limiter_preserves_silence_and_zero_underflow(module):
    async def run():
        peer = module.DeviceRtcPeer(FakeRtc(), asyncio.Queue())
        try:
            await peer.start()
            for _ in range(4):
                await peer.append_audio(bytes(960))
            for index in range(5):
                frame = await peer.track.recv()
                assert bytes(frame.planes[0]) == bytes(1920)
                assert frame.pts == index * 960
            limiter = peer.diagnostics()['downlinkPeakLimiter']
            assert limiter['resampledFrames'] == 4 and limiter['limitedFrames'] == 0
            assert limiter['preLimitPeakMax'] == 0 and limiter['minScale'] == 1
            assert peer.diagnostics()['downlinkTrackEnergy']['peak'] == 0
        finally:
            await peer.close()
    asyncio.run(run())


def test_native_48k_downlink_is_sample_exact_without_a_resampler(module):
    async def run():
        peer = module.DeviceRtcPeer(FakeRtc(), asyncio.Queue())
        pcm = struct.pack('<960h', *([1000, -1000] * 480))
        try:
            await peer.start()
            await peer.append_audio(pcm, sample_rate=48000)
            frame = await peer.track.recv()
            assert bytes(frame.planes[0]) == pcm
            diagnostics = peer.diagnostics()['downlinkPeakLimiter']
            assert diagnostics['resampledFrames'] == 0
            assert diagnostics['passthroughFrames'] == 1
            assert diagnostics['inputSampleRate'] == 48000
        finally:
            await peer.close()
    asyncio.run(run())


def test_downlink_rate_change_is_rejected_before_mutating_live_filter(module):
    async def run():
        peer = module.DeviceRtcPeer(FakeRtc(), asyncio.Queue())
        try:
            await peer.start()
            await peer.append_audio(bytes(1920), sample_rate=48000)
            before = peer.diagnostics()
            with pytest.raises(ValueError, match='rate'):
                await peer.append_audio(bytes(960), sample_rate=24000)
            assert peer.diagnostics()['bufferedBytes'] == before['bufferedBytes']
        finally:
            await peer.close()
    asyncio.run(run())


def test_downlink_float_resampler_is_continuous_across_input_chunks(module):
    async def run():
        values = [round(6000 * math.sin(2 * math.pi * 440 * index / 24000))
                  for index in range(1440)]
        pcm = struct.pack('<1440h', *values)
        reference = av.AudioResampler(format='flt', layout='mono', rate=48000)
        frames = reference.resample(pcm_frame(values, rate=24000))
        raw = [round(value * 32768) for output in frames
               for (value,) in struct.iter_unpack('=f', bytes(output.planes[0])[:output.samples * 4])]
        expected = struct.pack(f'<{len(raw)}h', *raw)
        track = module.DeviceAudioTrack()
        try:
            for first, last in [(0, 320), (320, 800), (800, 1440)]:
                track.push(pcm[first * 2:last * 2])
            assert bytes(track.buffer) == expected
            assert 0 <= 1440 * 4 - len(track.buffer) <= 128, 'Only the persistent filter tail'
            assert track.input_samples == 1440
            assert track.resampler.format.name == 'flt'
            output = []
            for _ in range(3):
                frame = await track.recv()
                assert frame.sample_rate == 48000 and frame.layout.name == 'mono'
                assert frame.format.name == 's16'
                output.append(frame)
            assert [frame.pts for frame in output] == [0, 960, 1920]
            assert b''.join(bytes(frame.planes[0]) for frame in output) == expected + bytes(5760 - len(expected))
        finally:
            track.stop()
    asyncio.run(run())


def test_runtime_failure_replaces_full_queue_without_retaining_payloads(module):
    async def run():
        rtc, events = FakeRtc(), asyncio.Queue(maxsize=1)
        peer = module.DeviceRtcPeer(rtc, events)
        await peer.start()
        events.put_nowait({'method': 'ordinary', 'secret': 'private-payload'})
        rtc.emit('evt.rtc.state', state='failed', reason='private-token-SDP')
        await wait_until(lambda: peer.diagnostics()['failed'])
        assert events.qsize() == 1
        event = events.get_nowait()
        assert event['method'] == 'local/error'
        assert 'private' not in json.dumps(peer.diagnostics())
        assert 'private' not in json.dumps(event)
        await peer.close()
    asyncio.run(run())


def test_pcm_consumer_backpressure_is_fatal_not_silent_drop(module):
    async def run():
        events = asyncio.Queue(maxsize=1)
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        await peer.start()
        events.put_nowait({'method': 'ordinary'})
        class Source:
            kind = 'audio'
            async def recv(self):
                frame = av.AudioFrame(format='s16', layout='mono', samples=320)
                frame.sample_rate = 16000
                frame.planes[0].update(bytes(640))
                return frame
        peer.pc.callbacks['track'](Source())
        await wait_until(lambda: peer.diagnostics()['failed'])
        assert events.get_nowait()['method'] == 'local/error'
        await peer.close()
    asyncio.run(run())


def test_close_drains_late_start_and_repeated_cancellation(module):
    async def run():
        rtc = FakeRtc()
        rtc.start_release.clear()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        starting = asyncio.create_task(peer.start())
        await asyncio.to_thread(rtc.start_entered.wait, 1)
        closing = asyncio.create_task(peer.close())
        await asyncio.sleep(.02)
        closing.cancel()
        await asyncio.sleep(.01)
        closing.cancel()
        assert not closing.done() and not rtc.stop_entered.is_set()
        rtc.start_release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert rtc.state == 'stopped'
        assert not any(kind == 'offer' for kind, _ in rtc.calls)
        assert peer.diagnostics()['stopConfirmed']
    asyncio.run(run())


def test_stop_ack_is_drained_even_when_close_is_cancelled_twice(module):
    async def run():
        rtc = FakeRtc()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        await peer.start()
        rtc.stop_release.clear()
        closing = asyncio.create_task(peer.close())
        await asyncio.to_thread(rtc.stop_entered.wait, 1)
        closing.cancel()
        await asyncio.sleep(.01)
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done() and not peer.diagnostics()['stopConfirmed']
        rtc.stop_release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert peer.diagnostics()['stopConfirmed']
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['sdk', 'peer', 'no_ack'])
def test_failed_close_remains_retryable_and_never_confirms_early(module, failure):
    async def run():
        rtc = FakeRtc()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        await peer.start()
        pc = peer.pc
        if failure == 'sdk':
            rtc.stop_error = TimeoutError('lost stop ACK')
        elif failure == 'peer':
            pc.close_error = RuntimeError('peer close failed')
        else:
            rtc.stop_result = False
        with pytest.raises(RuntimeError, match='cleanup'):
            await peer.close()
        assert not peer.diagnostics()['stopConfirmed']
        if failure == 'peer':
            assert peer.pc is pc
        rtc.stop_error = pc.close_error = None
        rtc.stop_result = True
        await peer.close()
        assert peer.diagnostics()['stopConfirmed']
        if failure == 'peer':
            assert pc.close_calls == 2
    asyncio.run(run())


def test_connect_timeout_closes_sdk_lease_and_host_peer(module, monkeypatch):
    async def run():
        def create(configuration):
            pc = FakePeer(configuration)
            pc.connect_on_answer = False
            return pc
        monkeypatch.setattr(module, 'RTCPeerConnection', create)
        rtc = FakeRtc()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        with pytest.raises(asyncio.TimeoutError):
            await peer.start()
        assert rtc.state == 'stopped' and peer.diagnostics()['stopConfirmed']
    asyncio.run(run())


def test_cancelled_start_caller_drains_its_late_sdk_lease_twice(module):
    async def run():
        rtc = FakeRtc()
        rtc.start_release.clear()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        starting = asyncio.create_task(peer.start())
        await asyncio.to_thread(rtc.start_entered.wait, 1)
        starting.cancel()
        await asyncio.sleep(.01)
        starting.cancel()
        await asyncio.sleep(.01)
        assert not starting.done()
        rtc.start_release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert peer.diagnostics()['stopConfirmed'] and rtc.state == 'stopped'
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['invalid_answer', 'invalid_candidate', 'history_gap'])
def test_signalling_failure_is_an_error_not_user_cancellation(module, failure):
    async def run():
        rtc, events = FakeRtc(), asyncio.Queue(maxsize=2)
        def offer(_):
            if failure == 'invalid_answer':
                rtc.emit('evt.rtc.signal', kind='answer', sdp=123)
            elif failure == 'invalid_candidate':
                rtc.emit('evt.rtc.signal', kind='candidate', candidate='not-an-ICE-candidate')
            else:
                rtc.emit('evt.rtc.state', state='starting')
                rtc.messages[0]['id'] = 3
        rtc.send_offer = offer
        peer = module.DeviceRtcPeer(rtc, events)
        with pytest.raises(RuntimeError):
            await peer.start()
        assert peer.diagnostics()['failed'] and peer.diagnostics()['stopConfirmed']
        assert events.get_nowait()['method'] == 'local/error'
    asyncio.run(run())


def test_failed_signal_during_inflight_offer_is_not_user_cancellation(module):
    async def run():
        rtc = FakeRtc()
        entered, release = threading.Event(), threading.Event()
        def offer(_):
            entered.set()
            assert release.wait(3)
        rtc.send_offer = offer
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        starting = asyncio.create_task(peer.start())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            rtc.emit('evt.rtc.state', state='failed')
            await wait_until(lambda: peer.diagnostics()['failed'])
        finally:
            release.set()
        with pytest.raises(RuntimeError):
            await starting
        assert peer.diagnostics()['stopConfirmed']
    asyncio.run(run())


def test_media_stats_allowlist_proves_both_directions_without_payloads(module):
    async def run():
        rtc = FakeRtc()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        await peer.start()
        counters = dict(audio_capture_frames=100, audio_capture_bytes=64000, audio_tx_packets=100,
                        audio_tx_bytes=8000, audio_tx_errors=0,
                        audio_tx_dropped_frames=2, audio_tx_dropped_bytes=1280,
                        audio_packets=80, audio_decoded_frames=80,
                        audio_i2s_bytes=51200, audio_render_errors=0,
                        audio_queue_ms=20, audio_queue_dropped=1,
                        audio_pipeline_age_ewma_us=20000, audio_pipeline_age_max_us=32000,
                        audio_capture_peak=4096, audio_microphone_peak=4096, audio_pcm_peak=2048,
                        audio_tx_last_error=-7)
        rtc.emit('evt.rtc.stats', **counters, audio_aec_active=True,
                 audio_aec_reference_bytes=51200, audio_aec_process_max_us=12000,
                 audio_opus_encode_ewma_us=1000, sdp='private-sdp',
                 pcm='private-pcm', token='private-token')
        await wait_until(lambda: 'evt.rtc.stats' in peer.diagnostics()['eventTypes'])
        diagnostics = peer.diagnostics()
        assert diagnostics['media'] == counters
        assert diagnostics['aec']['audio_aec_active'] is True
        assert diagnostics['aec']['audio_aec_process_max_us'] == 12000
        assert diagnostics['aec']['audio_opus_encode_ewma_us'] == 1000
        assert 'private' not in json.dumps(diagnostics)
        diagnostics['media']['audio_tx_bytes'] = 0
        assert peer.diagnostics()['media']['audio_tx_bytes'] == 8000
        rtc.emit('evt.rtc.stats', audio_tx_bytes=True, audio_packets=-1,
                 audio_i2s_bytes='private', audio_queue_ms=1.5)
        await wait_until(lambda: peer._cursor == len(rtc.messages))
        assert peer.diagnostics()['media'] == {}
        await peer.close()
    asyncio.run(run())


def test_existing_sdk_session_is_not_taken_over_or_stopped(module):
    async def run():
        rtc = FakeRtc()
        rtc.active, rtc.state = True, 'connected'
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        with pytest.raises(RuntimeError, match='active'):
            await peer.start()
        assert rtc.active
        assert not rtc.calls, 'Do not stop an RTC lease owned by another caller'
    asyncio.run(run())


@pytest.mark.parametrize('acknowledged', [True, False])
def test_stop_ack_or_local_stopped_state_is_not_a_device_release_receipt(module, acknowledged):
    async def run():
        rtc = FakeRtc()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        await peer.start()
        def stop_without_device_receipt():
            rtc.active, rtc.state = False, 'stopped'
            return acknowledged
        rtc.stop = stop_without_device_receipt
        with pytest.raises(RuntimeError, match='cleanup'):
            await peer.close()
        assert not peer.diagnostics()['stopConfirmed']
        # A late public SDK event can confirm physical cleanup on a later retry;
        # a local snapshot or another accepted-but-stopping ACK cannot.
        rtc.emit('evt.rtc.state', state='stopped')
        await peer.close()
        assert peer.diagnostics()['stopConfirmed']
    asyncio.run(run())


def test_opus_offer_uses_real_aiortc_without_opening_media_or_network(module):
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCRtpSender
    async def run():
        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        track = module.DeviceAudioTrack()
        try:
            transceiver = pc.addTransceiver(track, direction='sendrecv')
            transceiver.setCodecPreferences([codec for codec in RTCRtpSender.getCapabilities('audio').codecs
                                            if codec.mimeType.lower() == 'audio/opus'])
            # createOffer alone does not bind ICE sockets or use a host microphone.
            offer = await pc.createOffer()
            assert 'opus/48000/2' in offer.sdp
            assert 'PCMU' not in offer.sdp and 'PCMA' not in offer.sdp
            assert 'a=sendrecv' in offer.sdp
        finally:
            track.stop()
            await pc.close()
    asyncio.run(run())


def test_real_application_rtc_stop_ack_loss_is_retryable(module):
    from concurrent.futures import Future
    from watcherobot.application.rtc import ApplicationRtc
    class Transport:
        def __init__(self):
            self.listeners = []
            self.sent = []
            self.ack_stop = False
        def add_message_listener(self, listener):
            self.listeners.append(listener)
        def remove_message_listener(self, listener):
            self.listeners.remove(listener)
        def send_device(self, frame):
            message = json.loads(frame)
            self.sent.append(message)
            event = {key: message[key] for key in ('protocol', 'client_id', 'session_id', 'command_id')}
            if message['type'] == 'ctrl.rtc.session.start' or (
                    message['type'] == 'ctrl.rtc.session.stop' and self.ack_stop):
                event.update(type='sys.ack', data={})
                for listener in self.listeners:
                    listener(event)
                if message['type'] == 'ctrl.rtc.session.stop':
                    stopped = {**event, 'type': 'evt.rtc.state', 'data': {'state': 'stopped'}}
                    for listener in self.listeners:
                        listener(stopped)
            elif message['type'] == 'ctrl.rtc.signal':
                event.update(type='evt.rtc.signal', data={'kind': 'answer', 'sdp': 'device-answer'})
                for listener in self.listeners:
                    listener(event)
            future = Future()
            future.set_result(None)
            return future
    async def run():
        transport = Transport()
        rtc = ApplicationRtc(transport, send_timeout=.05)
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        await peer.start()
        with pytest.raises(RuntimeError, match='cleanup'):
            await peer.close()
        assert rtc.snapshot()['active'] and not peer.diagnostics()['stopConfirmed']
        transport.ack_stop = True
        await peer.close()
        assert not rtc.snapshot()['active'] and peer.diagnostics()['stopConfirmed']
        assert [m['type'] for m in transport.sent].count('ctrl.rtc.session.stop') == 2
        assert all(m['type'].startswith('ctrl.rtc.') for m in transport.sent)
        rtc.close()
    asyncio.run(run())


def pcm_frame(samples, *, rate=16000, channels=1, pts=0):
    frame = av.AudioFrame(format='s16', layout='mono' if channels == 1 else 'stereo',
                          samples=len(samples) // channels)
    frame.sample_rate = rate
    frame.pts, frame.time_base = pts, Fraction(1, rate)
    frame.planes[0].update(struct.pack('<' + 'h' * len(samples), *samples))
    return frame


def test_pcm_energy_is_sample_weighted_and_retains_no_audio(module):
    energy = module.PcmEnergy()
    energy.add(struct.pack('<4h', 1000, -1000, 1000, -1000))
    first = energy.snapshot()
    assert first['lastPeak'] == 1000 and first['lastRms'] == 1000
    assert first['lastEnergy'] == 4_000_000 and first['lastMeanSquare'] == 1_000_000
    assert first['lastRmsDbfs'] == pytest.approx(20 * math.log10(1000 / 32768), abs=.001)
    energy.add(bytes(8))
    assert energy.snapshot()['silentFrames'] == 1
    assert energy.snapshot()['lastRmsDbfs'] is None
    energy.add(struct.pack('<2h', -32768, 32767))
    last = energy.snapshot()
    assert last['peak'] == 32768 and last['clippedSamples'] == 2
    assert last['samples'] == 10
    assert last['rms'] == pytest.approx(math.sqrt((4_000_000 + 32768**2 + 32767**2) / 10), abs=.001)
    assert all(not isinstance(value, (bytes, bytearray, list, av.AudioFrame))
               for value in vars(energy).values())
    json.dumps(last, allow_nan=False)


def test_receive_timestamps_energy_and_relative_clock_drift_do_not_change_pcm(module, monkeypatch):
    async def run():
        events = asyncio.Queue(maxsize=8)
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        await peer.start()
        clock = [10.0]
        monkeypatch.setattr(peer, '_now', lambda: clock[0])
        class Source:
            kind = 'audio'
            index = 0
            async def recv(self):
                if self.index == 2:
                    await asyncio.Future()
                clock[0] = 10 + self.index * .035
                result = pcm_frame([self.index * 1000] * 320, pts=self.index * 320)
                self.index += 1
                return result
        peer.pc.callbacks['track'](Source())
        await wait_until(lambda: events.qsize() == 2)
        first, second = events.get_nowait(), events.get_nowait()
        timing = first['params']['timing']
        assert timing['clock'] == 'host-monotonic'
        assert timing['sourceReceivedAt'] == timing['enqueuedAt'] == 10
        assert timing['sourcePtsSeconds'] == 0 and timing['sourceDurationMs'] == 20
        assert timing['queueDepthAtEnqueue'] == 1
        # Exact residence is a consumer observation, not inferred from PTS.
        assert (10.125 - timing['enqueuedAt']) * 1000 == 125
        assert second['params']['timing']['sourceFrameSequence'] == 2
        assert base64.b64decode(second['params']['audio']['data']) == struct.pack('<320h', *([1000] * 320))
        clock[0] = 10.085
        diagnostics = peer.diagnostics()
        assert diagnostics['receiveClock']['sourceFrames'] == 2
        assert diagnostics['receiveClock']['sourceRate'] == 16000
        assert diagnostics['receiveClock']['arrivalMediaSkewMs'] == pytest.approx(15)
        assert diagnostics['receiveClock']['lastFrameAgoMs'] == pytest.approx(50)
        assert diagnostics['receivedAudioEnergy']['lastRms'] == 1000
        assert diagnostics['eventTiming']['queueDepthMax'] == 2
        assert diagnostics['eventTiming']['queueDepth'] == 0
        assert 'residenceMs' not in diagnostics['eventTiming'], 'Consumer metrics belong to service'
        await peer.close()
    asyncio.run(run())


def test_receive_48k_stereo_resamples_without_seconds_of_aggregation(module):
    async def run():
        events = asyncio.Queue(maxsize=8)
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        await peer.start()
        class Source:
            kind = 'audio'
            index = 0
            async def recv(self):
                if self.index == 3:
                    await asyncio.Future()
                frame = pcm_frame([1000, 1000] * 960, rate=48000, channels=2, pts=self.index * 960)
                self.index += 1
                return frame
        peer.pc.callbacks['track'](Source())
        await wait_until(lambda: peer.diagnostics()['receivedFrames'] == 3)
        events_received = [events.get_nowait() for _ in range(3)]
        samples = sum(event['params']['audio']['samplesPerChannel'] for event in events_received)
        assert 0 <= 960 - samples <= 32, 'Only the persistent resampler filter tail, not a batching delay'
        assert peer.diagnostics()['receivedBytes'] == samples * 2
        clock = peer.diagnostics()['receiveClock']
        assert clock['sourceRate'] == 48000 and clock['sourceChannels'] == 2
        assert clock['sourceDurationMs'] == 20
        assert peer.diagnostics()['receivedAudioEnergy']['lastRms'] == pytest.approx(1000, abs=1)
        await peer.close()
    asyncio.run(run())


def test_receive_clock_missing_and_regressing_pts_are_diagnostics_not_audio_failure(module):
    async def run():
        peer = module.DeviceRtcPeer(FakeRtc(), asyncio.Queue())
        for pts, when in [(None, 1), (320, 1.02), (0, 1.04)]:
            frame = pcm_frame([0] * 320, pts=pts)
            peer._note_source_frame(frame, when)
        diagnostics = peer.diagnostics()
        assert not diagnostics['failed']
        assert diagnostics['receiveClock']['missingPtsFrames'] == 1
        assert diagnostics['receiveClock']['ptsRegressions'] == 1
        assert diagnostics['receiveClock']['arrivalMediaSkewMs'] == 0
        json.dumps(diagnostics, allow_nan=False)
        await peer.close()
    asyncio.run(run())


def test_downlink_energy_exposes_resampler_gain_without_altering_audio(module):
    async def run():
        peer = module.DeviceRtcPeer(FakeRtc(), asyncio.Queue())
        await peer.start()
        await peer.append_audio(struct.pack('<2400h', *([2000] * 2400)))
        await peer.track.recv()
        frame = await peer.track.recv()
        diagnostics = peer.diagnostics()
        assert diagnostics['downlinkInputEnergy']['lastRms'] == 2000
        assert diagnostics['downlinkTrackEnergy']['lastRms'] == pytest.approx(2000, abs=1)
        assert bytes(frame.planes[0]) == struct.pack('<960h', *([2000] * 960))
        await peer.close()
    asyncio.run(run())


def test_full_device_audio_stats_are_bounded_numeric_and_allowlisted(module):
    async def run():
        rtc = FakeRtc()
        peer = module.DeviceRtcPeer(rtc, asyncio.Queue())
        await peer.start()
        rtc.emit('evt.rtc.stats', audio_tx_last_error=-7, audio_pcm_rms=1234,
                 audio_raw_rms=1000, audio_output_rms=2000, audio_output_peak=4000,
                 audio_gain_db_x100=-150, audio_limiter_hits=2, audio_aec_active=1,
                 audio_microphone_read_max_us=64657, audio_aec_process_max_us=50000,
                 audio_opus_encode_max_us=58192, audio_pipeline_age_max_us=236085,
                 audio_tx_dropped_frames=15992, token='private-credential',
                 audio_tx_bytes=2**100, audio_aec_chunks=True)
        await wait_until(lambda: 'evt.rtc.stats' in peer.diagnostics()['eventTypes'])
        diagnostics = peer.diagnostics()
        assert diagnostics['media']['audio_tx_last_error'] == -7
        assert diagnostics['media']['audio_pcm_rms'] == 1234
        assert diagnostics['media']['audio_raw_rms'] == 1000
        assert diagnostics['media']['audio_output_rms'] == 2000
        assert diagnostics['media']['audio_gain_db_x100'] == -150
        assert diagnostics['media']['audio_limiter_hits'] == 2
        assert diagnostics['media']['audio_pipeline_age_max_us'] == 236085
        assert diagnostics['aec']['audio_microphone_read_max_us'] == 64657
        assert 'audio_tx_bytes' not in diagnostics['media']
        assert 'audio_aec_chunks' not in diagnostics['aec']
        assert 'audio_aec_active' not in diagnostics['aec'], 'AEC activity must be an actual boolean'
        assert diagnostics['deviceStatsAgeMs'] >= 0
        assert 'private' not in json.dumps(diagnostics, allow_nan=False)
        await peer.close()
    asyncio.run(run())


def test_event_enqueue_timestamp_includes_encoding_work_and_reports_large_skew_without_drop(module, monkeypatch):
    async def run():
        events = asyncio.Queue(maxsize=8)
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        await peer.start()
        clock = [20.0]
        monkeypatch.setattr(peer, '_now', lambda: clock[0])
        original_encode = module.base64.b64encode
        def delayed_encode(pcm):
            clock[0] += .012
            return original_encode(pcm)
        monkeypatch.setattr(module.base64, 'b64encode', delayed_encode)
        class Source:
            kind = 'audio'
            index = 0
            async def recv(self):
                if self.index == 2:
                    await asyncio.Future()
                clock[0] = 20 + self.index * .18
                frame = pcm_frame([1000] * 320, pts=self.index * 320)
                self.index += 1
                return frame
        peer.pc.callbacks['track'](Source())
        await wait_until(lambda: events.qsize() == 2)
        first, second = events.get_nowait(), events.get_nowait()
        assert first['params']['timing']['enqueuedAt'] == pytest.approx(20.012)
        assert first['params']['timing']['processingMs'] == pytest.approx(12)
        assert second['params']['timing']['sourceReceivedAt'] == pytest.approx(20.18)
        assert peer.diagnostics()['receiveClock']['arrivalMediaSkewMs'] == pytest.approx(160)
        assert peer.diagnostics()['receivedFrames'] == 2 and not peer.diagnostics()['failed']
        await peer.close()
    asyncio.run(run())


def test_diagnostics_remain_constant_shape_finite_and_do_not_retain_frame_history(module):
    async def run():
        peer = module.DeviceRtcPeer(FakeRtc(), asyncio.Queue())
        frame = pcm_frame([1000] * 320)
        first_size = None
        for index in range(1000):
            frame.pts = index * 320
            peer._note_source_frame(frame, 30 + index * .02)
            peer._received_energy.add(bytes(frame.planes[0]))
            if index == 0:
                first_size = len(json.dumps(peer.diagnostics(), allow_nan=False))
        snapshot = peer.diagnostics()
        assert snapshot['receiveClock']['sourceFrames'] == 1000
        assert len(json.dumps(snapshot, allow_nan=False)) <= first_size + 300
        assert all(not isinstance(value, av.AudioFrame) for value in vars(peer).values())
        for metrics in (peer._receive_clock, peer._event_timing, vars(peer._received_energy)):
            assert all(not isinstance(value, (bytes, bytearray, list, av.AudioFrame))
                       for value in metrics.values())
        snapshot['receivedAudioEnergy']['peak'] = 0
        assert peer.diagnostics()['receivedAudioEnergy']['peak'] == 1000
        await peer.close()
    asyncio.run(run())


def test_source_watch_has_no_preconnection_timeout_and_full_connected_startup_grace(module, monkeypatch):
    async def run():
        def create(configuration):
            pc = FakePeer(configuration)
            pc.connect_on_answer = False
            return pc
        monkeypatch.setattr(module, 'RTCPeerConnection', create)
        events = asyncio.Queue(maxsize=2)
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        clock = [100.0]
        monkeypatch.setattr(peer, '_now', lambda: clock[0])
        starting = asyncio.create_task(peer.start())
        await wait_until(lambda: peer.pc is not None and peer.pc.remoteDescription is not None)
        clock[0] = 200.0
        peer._check_source(clock[0])
        assert not peer.diagnostics()['failed'], 'Signalling time is not source stall time'
        await peer.pc.connect()
        await starting
        clock[0] = 202.999
        peer._check_source(clock[0])
        # Duplicate connected callbacks must not continually renew the grace.
        await peer.pc.connect()
        clock[0] = 203.0
        peer._check_source(clock[0])
        assert not peer.diagnostics()['failed']
        clock[0] = 203.001
        await wait_until(lambda: peer.diagnostics()['failed'])
        assert events.get_nowait()['method'] == 'local/error'
        assert peer.diagnostics()['sourceWatch']['stalled']
        await peer.close()
    asyncio.run(run())


def test_continuing_silent_source_frames_refresh_watch_without_conversation_timeout(module, monkeypatch):
    async def run():
        events = asyncio.Queue(maxsize=8)
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        clock = [100.0]
        monkeypatch.setattr(peer, '_now', lambda: clock[0])
        await peer.start()
        class Source:
            kind = 'audio'
            frames = asyncio.Queue()
            async def recv(self):
                return await self.frames.get()
        source = Source()
        peer.pc.callbacks['track'](source)
        for index in range(20):
            clock[0] += 2
            source.frames.put_nowait(pcm_frame([0] * 320, pts=index * 320))
            await wait_until(lambda: peer.diagnostics()['receivedFrames'] == index + 1)
            event = events.get_nowait()
            assert event['method'] == 'local/deviceAudio'
            assert set(base64.b64decode(event['params']['audio']['data'])) == {0}
            peer._check_source(clock[0] + 2.999)
            assert not peer.diagnostics()['failed']
        assert peer.diagnostics()['receivedAudioEnergy']['silentFrames'] == 20
        assert peer.diagnostics()['sourceWatch']['armed']
        await peer.close()
    asyncio.run(run())


def test_no_source_frame_stall_is_fatal_even_while_sdk_and_downlink_remain_live(module, monkeypatch):
    async def run():
        rtc, events = FakeRtc(), asyncio.Queue(maxsize=2)
        peer = module.DeviceRtcPeer(rtc, events)
        clock = [100.0]
        monkeypatch.setattr(peer, '_now', lambda: clock[0])
        await peer.start()
        class Source:
            kind = 'audio'
            drained = False
            sent = False
            async def recv(self):
                if not self.sent:
                    self.sent = True
                    clock[0] = 101.0
                    return pcm_frame([0] * 320)
                try:
                    await asyncio.Future()
                finally:
                    self.drained = True
        source = Source()
        peer.pc.callbacks['track'](source)
        await wait_until(lambda: peer.diagnostics()['receivedFrames'] == 1)
        events.get_nowait()
        clock[0] = 103.999
        await peer.append_audio(b'\x01\x00' * 480)
        rtc.emit('evt.rtc.stats', audio_tx_packets=100, token='private-payload')
        await wait_until(lambda: peer.diagnostics()['media'].get('audio_tx_packets') == 100)
        peer._check_source(clock[0])
        assert not peer.diagnostics()['failed']
        clock[0] = 104.001
        await wait_until(lambda: peer.diagnostics()['failed'])
        event = events.get_nowait()
        assert event['method'] == 'local/error' and 'source stalled' in event['params']['message']
        assert len(json.dumps(event)) < 256 and 'private' not in json.dumps(event)
        for _ in range(10):
            peer._check_source(clock[0] + 10)
        assert events.empty(), 'Only one bounded fatal event for the stall'
        await peer.close()
        assert source.drained and peer.diagnostics()['stopConfirmed']
    asyncio.run(run())


def test_source_watch_does_not_report_an_explicit_close_as_a_stall(module, monkeypatch):
    async def run():
        events = asyncio.Queue(maxsize=2)
        peer = module.DeviceRtcPeer(FakeRtc(), events)
        clock = [100.0]
        monkeypatch.setattr(peer, '_now', lambda: clock[0])
        await peer.start()
        await peer.close()
        clock[0] = 1000.0
        peer._check_source(clock[0])
        assert events.empty() and not peer.diagnostics()['failed']
        assert not peer.diagnostics()['sourceWatch']['armed']
    asyncio.run(run())


@pytest.mark.parametrize('initial,changed', [(24000, 48000), (48000, 24000)])
def test_rate_switch_preserves_all_track_state(module, initial, changed):
    track = module.DeviceAudioTrack()
    track.push(bytes(960), sample_rate=initial)
    before = (bytes(track.buffer), track.input_samples, dict(track.peak_limiter),
              track.input_energy.snapshot())
    with pytest.raises(ValueError, match='rate'):
        track.push(bytes(960), sample_rate=changed)
    assert before == (bytes(track.buffer), track.input_samples, track.peak_limiter,
                      track.input_energy.snapshot())
    track.stop()


def test_native_backlog_exact_boundary_rejects_without_state_mutation(module):
    async def run():
        track = module.DeviceAudioTrack()
        track.push(b'\x01\x00' * 96000, sample_rate=48000)
        before = (bytes(track.buffer), track.input_samples, dict(track.peak_limiter))
        with pytest.raises(RuntimeError, match='backlog'):
            track.push(bytes(2), sample_rate=48000)
        assert before == (bytes(track.buffer), track.input_samples, track.peak_limiter)
        await track.recv()
        track.push(bytes(1920), sample_rate=48000)
        assert len(track.buffer) == 192000
        track.stop()
    asyncio.run(run())


def test_native_zero_burst_is_idle_but_in_speech_pauses_are_preserved(module):
    async def run():
        track = module.DeviceAudioTrack()
        for _ in range(150):
            track.push(bytes(1920), sample_rate=48000)
        assert not track.buffer
        assert track.peak_limiter['idleSkippedFrames'] == 150
        first, last = b'\xe8\x03' * 960, b'\xd0\x07' * 960
        track.push(first, sample_rate=48000)
        track.push(bytes(1920), sample_rate=48000)
        track.push(last, sample_rate=48000)
        assert bytes(track.buffer) == first + bytes(1920) + last
        frames = [await track.recv() for _ in range(3)]
        assert b''.join(bytes(frame.planes[0]) for frame in frames) == first + bytes(1920) + last
        track.stop()
    asyncio.run(run())


def test_v3_native_chain_has_no_second_frame_gain_or_resampling(module):
    from playback_gain import PlaybackGain
    gain = PlaybackGain(sample_rate=48000)
    source = struct.pack('<48000h', *([1000] * 23999 + [32767, -32768] + [1000] * 23999))
    output = gain.process(source) + gain.flush()
    track = module.DeviceAudioTrack()
    track.push(output, sample_rate=48000)
    assert bytes(track.buffer) == output
    assert track.peak_limiter['resampledFrames'] == 0
    assert track.peak_limiter['limitedFrames'] == 0
    assert track.peak_limiter['minScale'] == 1
    track.stop()
