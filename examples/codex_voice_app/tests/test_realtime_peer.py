import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from realtime_peer import RobotAudioTrack


def test_offer_includes_realtime_events_data_channel():
    from realtime_peer import RealtimePeer
    async def run():
        peer = RealtimePeer(asyncio.Queue())
        try:
            assert 'm=application' in await peer.offer()
            assert peer.channel.label == 'oai-events'
        finally:
            await peer.close()
    asyncio.run(run())


def test_v3_transcript_events_are_normalized_without_audio_duplication():
    from realtime_peer import normalize_data_event
    assert normalize_data_event({'type': 'input_transcript.added', 'item': {'text': '你好'}}) == {
        'method': 'thread/realtime/transcript/delta', 'params': {'role': 'user', 'delta': '你好'}}
    assert normalize_data_event({'type': 'turn.done', 'turn': {'role': 'assistant', 'transcript': '你好呀'}}) == {
        'method': 'thread/realtime/transcript/done', 'params': {'role': 'assistant', 'text': '你好呀'}}
    assert normalize_data_event({'type': 'output_audio.delta', 'delta': 'not-used'}) is None


def test_robot_pcm_is_paced_without_using_host_microphone():
    async def run():
        track = RobotAudioTrack()
        track.push(b'\x01\x00' * 640)
        first, second, third = await track.recv(), await track.recv(), await track.recv()
        assert first.sample_rate == 16000
        assert first.samples == 320
        assert first.pts == 0 and second.pts == 320 and third.pts == 640
        assert bytes(first.planes[0]) == b'\x01\x00' * 320
        assert bytes(third.planes[0]) == b'\x00\x00' * 320
        track.stop()
    asyncio.run(run())


def test_oversized_audio_backlog_does_not_grow_indefinitely():
    track = RobotAudioTrack()
    with pytest.raises(ValueError):
        track.push(b'x')
    track.push(b'\x00\x00' * 16000)
    with pytest.raises(RuntimeError, match='backlog'):
        track.push(b'xx')


def test_upstream_queue_diagnostics_distinguish_wait_from_network_latency():
    async def run():
        now = [100.0]
        track = RobotAudioTrack(clock=lambda: now[0])
        track.push(b'\x01\x00' * 640)
        assert track.diagnostics()['bufferMs'] == 40
        now[0] += .08
        await track.recv()
        stats = track.diagnostics()
        assert stats['queueResidenceMs'] == pytest.approx(80)
        assert stats['bufferMs'] == 20
        assert stats['bufferHighWaterMs'] == 40
        track.buffer.clear()  # Existing logical-mute contract.
        await track.recv()
        assert track.diagnostics()['bufferMs'] == 0
        track.stop()
    asyncio.run(run())


def test_upstream_recovers_live_latency_instead_of_replaying_old_backlog():
    async def run():
        track = RobotAudioTrack()
        # Independent capture and host clocks must not accumulate seconds of
        # delayed speech. Keep the latest six 20 ms packets with visible loss.
        import struct
        for value in range(10):
            track.push(struct.pack('<h', value) * 320)
        frame = await track.recv()
        assert struct.unpack('<h', bytes(frame.planes[0])[:2])[0] == 4
        stats = track.diagnostics()
        assert stats['bufferMs'] == 100
        assert stats['freshnessDroppedMs'] == 80
        assert stats['freshnessRecoveryCount'] == 1
        assert frame.pts == 0, 'RTP sample timeline remains continuous after recovery'
        track.stop()
    asyncio.run(run())


def test_speak_waits_for_session_update_ack_not_only_data_channel_open():
    from types import SimpleNamespace
    from realtime_peer import RealtimePeer
    async def run():
        peer = RealtimePeer(asyncio.Queue())
        sent = []
        peer.channel = SimpleNamespace(readyState='open', send=lambda payload: sent.append(payload))
        try:
            speaking = asyncio.create_task(peer.speak('测试本轮回答'))
            await asyncio.sleep(.02)
            assert not sent
            peer.session_updated.set_result(None)
            await speaking
            assert 'speakable' in sent[0]
        finally:
            await peer.close()
    asyncio.run(run())


def test_voice_diagnostics_are_bounded_metadata_not_payloads():
    from types import SimpleNamespace
    from realtime_peer import RealtimePeer
    async def run():
        peer = RealtimePeer(asyncio.Queue(), voice_epoch=7)
        try:
            for index in range(40):
                peer.channel.emit('message', json.dumps({'type': 'unknown.event', 'secret': 'private-audio-content'}))
            peer.channel = SimpleNamespace(readyState='open', send=lambda payload: None)
            peer.session_updated.set_result(None)
            await peer.speak('这是private-text-content，不应出现在诊断中')
            diagnostic = peer.diagnostics()
            assert diagnostic['voiceEpoch'] == 7
            assert len(diagnostic['dataEvents']) == 24
            assert diagnostic['speakable'][0]['bytes'] > 0
            assert len(diagnostic['speakable'][0]['sha256']) == 64
            assert 'private' not in json.dumps(diagnostic)
            diagnostic['dataEvents'].clear()
            assert len(peer.diagnostics()['dataEvents']) == 24
        finally:
            await peer.close()
    asyncio.run(run())


def test_voice_diagnostics_count_rtp_before_silence_and_output_gates():
    import av
    from fractions import Fraction
    from realtime_peer import RealtimePeer
    async def run():
        peer = RealtimePeer(asyncio.Queue())
        class Track:
            index = 0
            async def recv(self):
                if self.index == 3:
                    raise asyncio.CancelledError()
                if self.index == 2:
                    peer.output_enabled = True
                frame = av.AudioFrame(format='s16', layout='mono', samples=240)
                frame.sample_rate = 24000
                frame.pts = self.index * 240
                frame.time_base = Fraction(1, 24000)
                frame.planes[0].update((b'\x00\x00' if self.index == 0 else b'\x00\x10') * 240)
                self.index += 1
                return frame
        try:
            with pytest.raises(asyncio.CancelledError):
                await peer._receive(Track())
            diagnostic = peer.diagnostics()
            assert diagnostic['receivedFrames'] == 3
            assert diagnostic['receivedBytes'] == 1440
            assert diagnostic['silentFrames'] == 1
            assert diagnostic['outputDisabledFrames'] == 1
            assert diagnostic['admittedBytes'] == 480
            assert diagnostic['peak'] == 4096
        finally:
            await peer.close()
    asyncio.run(run())


def test_realtime_output_is_admitted_before_any_backend_speak_request():
    from realtime_peer import RealtimePeer
    async def run():
        events = asyncio.Queue()
        peer = RealtimePeer(events, realtime=True, voice_epoch=3)
        try:
            peer._emit({'method': 'thread/realtime/transcript/delta',
                        'params': {'role': 'assistant', 'delta': '好，我看看'}})
            event = events.get_nowait()
            assert event['params']['voiceEpoch'] == 3
            assert peer.output_enabled
        finally:
            await peer.close()
    asyncio.run(run())


def test_backend_context_uses_correlated_delegation_and_bounded_chunks():
    from types import SimpleNamespace
    from realtime_peer import RealtimePeer
    async def run():
        peer = RealtimePeer(asyncio.Queue(), realtime=True)
        sent = []
        peer.channel = SimpleNamespace(readyState='open', send=lambda value: sent.append(json.loads(value)))
        peer.session_updated.set_result(None)
        try:
            await peer.context('结果：' + '桌子' * 400, delegation_id='d1', channel='speakable')
            assert len(sent) > 1
            assert all(e['type'] == 'delegation.context.append' and e['delegation_item_id'] == 'd1' for e in sent)
            assert all(len(e['content'][0]['text'].encode()) <= 500 for e in sent)
            assert all(e['channel'] == 'speakable' for e in sent)
        finally:
            await peer.close()
    asyncio.run(run())


def test_native_realtime_keeps_48k_samples_without_a_rate_round_trip():
    import av
    import base64
    import struct
    from fractions import Fraction
    from realtime_peer import RealtimePeer

    async def run():
        events = asyncio.Queue()
        peer = RealtimePeer(events, realtime=True)
        samples = [1000 if i % 2 else -1000 for i in range(960)]
        pcm = struct.pack('<960h', *samples)

        class Track:
            consumed = False
            async def recv(self):
                if self.consumed:
                    raise asyncio.CancelledError()
                self.consumed = True
                frame = av.AudioFrame(format='s16', layout='mono', samples=960)
                frame.sample_rate, frame.pts, frame.time_base = 48000, 0, Fraction(1, 48000)
                frame.planes[0].update(pcm)
                return frame
        try:
            with pytest.raises(asyncio.CancelledError):
                await peer._receive(Track())
            event = events.get_nowait()['params']['audio']
            assert event['sampleRate'] == 48000
            assert event['samplesPerChannel'] == 960
            assert base64.b64decode(event['data']) == pcm
            assert peer.diagnostics()['sampleRateConversions'] == 0
        finally:
            await peer.close()
    asyncio.run(run())


def test_native_stereo_to_mono_does_not_clip_correlated_full_scale_channels():
    import av
    import base64
    import struct
    from realtime_peer import RealtimePeer

    async def run():
        events = asyncio.Queue()
        peer = RealtimePeer(events, realtime=True)
        pairs = [30000, 30000, -30000, -30000] * 480
        class Track:
            consumed = False
            async def recv(self):
                if self.consumed:
                    raise asyncio.CancelledError()
                self.consumed = True
                frame = av.AudioFrame(format='s16', layout='stereo', samples=960)
                frame.sample_rate = 48000
                frame.planes[0].update(struct.pack('<1920h', *pairs))
                return frame
        try:
            with pytest.raises(asyncio.CancelledError):
                await peer._receive(Track())
            pcm = base64.b64decode(events.get_nowait()['params']['audio']['data'])
            assert list(struct.unpack('<960h', pcm)) == [30000, -30000] * 480
        finally:
            await peer.close()
    asyncio.run(run())


@pytest.mark.parametrize('fmt', ['s16', 's16p', 'flt', 'fltp'])
@pytest.mark.parametrize('channels', [1, 2])
def test_native_format_normalization_is_continuous_and_unity(fmt, channels):
    import av
    import base64
    import struct
    from realtime_peer import RealtimePeer
    async def run():
        events = asyncio.Queue()
        peer = RealtimePeer(events, realtime=True)
        class Track:
            index = 0
            async def recv(self):
                if self.index == 3:
                    raise asyncio.CancelledError()
                frame = av.AudioFrame(format=fmt, layout='mono' if channels == 1 else 'stereo', samples=960)
                frame.sample_rate = 48000
                count = 960 if frame.format.is_planar else 960 * channels
                data = (struct.pack('<h', 1000) if fmt.startswith('s16') else
                        struct.pack('=f', 1000 / 32768)) * count
                for plane in frame.planes:
                    plane.update(data)
                self.index += 1
                return frame
        try:
            with pytest.raises(asyncio.CancelledError):
                await peer._receive(Track())
            for _ in range(3):
                audio = events.get_nowait()['params']['audio']
                assert base64.b64decode(audio['data']) == struct.pack('<h', 1000) * 960
                assert audio['sampleRate'] == 48000
            assert peer.diagnostics()['sampleRateConversions'] == 0
        finally:
            await peer.close()
    asyncio.run(run())


def test_native_idle_silence_keeps_advancing_the_pcm_timeline():
    import av
    import base64
    from realtime_peer import RealtimePeer
    async def run():
        events = asyncio.Queue()
        peer = RealtimePeer(events, realtime=True)
        class Track:
            index = 0
            async def recv(self):
                if self.index == 50:
                    raise asyncio.CancelledError()
                self.index += 1
                frame = av.AudioFrame(format='s16', layout='mono', samples=960)
                frame.sample_rate = 48000
                frame.planes[0].update(bytes(1920))
                return frame
        try:
            with pytest.raises(asyncio.CancelledError):
                await peer._receive(Track())
            assert events.qsize() == 50
            assert all(base64.b64decode(events.get_nowait()['params']['audio']['data']) == bytes(1920)
                       for _ in range(50))
        finally:
            await peer.close()
    asyncio.run(run())
