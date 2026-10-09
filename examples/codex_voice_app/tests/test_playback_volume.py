"""Temporary attenuation is last in the PCM path, not an AGC target."""
import asyncio
import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from device_rtc_peer import DeviceAudioTrack
from test_continuous_service import service


def test_half_volume_applies_to_already_queued_audio_without_changing_pts():
    async def run():
        track = DeviceAudioTrack()
        samples = [29000, -29000, 1000, -1000] * 480
        source = struct.pack('<1920h', *samples)
        track.push(source, sample_rate=48000)
        track.playback_volume.set_level(.5)
        first = await track.recv()
        assert track.output_energy.last_peak == 14500
        track.playback_volume.set_level(1)
        second = await track.recv()
        assert struct.unpack('<960h', bytes(first.planes[0])) == tuple(v // 2 for v in samples[:960])
        assert bytes(second.planes[0]) == source[1920:]
        assert (first.pts, second.pts, first.samples, second.samples) == (0, 960, 960, 960)
        assert track.input_energy.peak == track.output_energy.peak == 29000
        assert not track.buffer
        track.stop()
    asyncio.run(run())


def test_volume_identity_mute_and_sign_symmetry():
    from playback_volume import PlaybackVolume
    volume = PlaybackVolume()
    source = struct.pack('<6h', -32768, -999, -1, 1, 999, 32767)
    assert volume.process(source) is source
    volume.set_level(.5)
    assert struct.unpack('<6h', volume.process(source)) == (-16384, -500, 0, 0, 500, 16384)
    assert volume.diagnostics()['gainDb'] == -6.02
    volume.set_level(0)
    assert volume.process(source) == bytes(len(source))
    assert volume.diagnostics()['gainDb'] is None


def test_volume_changes_while_recv_waits_apply_to_the_next_generated_frame():
    async def run():
        clock, entered, release = [0.0], asyncio.Event(), asyncio.Event()
        async def waiting_sleep(delay):
            entered.set()
            await release.wait()
            clock[0] += delay
        track = DeviceAudioTrack(clock=lambda: clock[0], sleep=waiting_sleep)
        track.push(b'\xe8\x03' * 1920, sample_rate=48000)
        await track.recv()
        receiving = asyncio.create_task(track.recv())
        await entered.wait()
        track.playback_volume.set_level(.5)
        release.set()
        frame = await receiving
        assert struct.unpack('<960h', bytes(frame.planes[0])) == (500,) * 960
        assert frame.pts == 960 and not track.buffer
        track.stop()
    asyncio.run(run())


@pytest.mark.parametrize('level', [True, False, '0.5', None, -.1, 1.1, math.nan, math.inf])
def test_invalid_volume_preserves_current_level_and_queued_audio(level):
    track = DeviceAudioTrack()
    track.push(b'\xe8\x03' * 960, sample_rate=48000)
    track.playback_volume.set_level(.5)
    before = bytes(track.buffer)
    with pytest.raises(ValueError):
        track.playback_volume.set_level(level)
    assert track.playback_volume.level == .5 and bytes(track.buffer) == before
    track.stop()


def test_live_volume_survives_stop_restart_but_not_a_new_application_instance():
    async def run():
        svc, robot, agent = service()
        await svc.set_playback_volume(.5)
        assert svc.snapshot()['playbackVolume'] == .5
        assert not agent.calls and not robot.opens
        await svc.start()
        assert svc.device_peer.track.playback_volume is svc.playback_volume
        peer = svc.device_peer
        await svc.set_playback_volume(.25)
        assert peer.track.playback_volume.level == .25
        await svc.stop()
        await svc.start()
        assert svc.device_peer.track.playback_volume.level == .25
        await svc.stop()
        assert service()[0].snapshot()['playbackVolume'] == 1
    asyncio.run(run())


def test_live_agc_cannot_undo_the_volume_setting():
    from test_continuous_service import deliver
    import base64
    async def run():
        svc, _, agent = service()
        await svc.set_playback_volume(.5)
        await svc.start()
        pcm = b'\xe8\x03' * 3840
        await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
                      dict(voiceEpoch=1, audio=dict(data=base64.b64encode(pcm).decode(),
                           sampleRate=48000, numChannels=1, samplesPerChannel=3840)))
        peer = svc.device_peer
        enhanced = bytes(peer.track.buffer)[:1920]
        frame = await peer.track.recv()
        expected = tuple(round(v * .5) for (v,) in struct.iter_unpack('<h', enhanced))
        assert struct.unpack('<960h', bytes(frame.planes[0])) == expected
        assert peer.track.output_energy.peak <= 14500
        assert svc.snapshot()['playbackGain']['profile'] == 'clear-speech-v4'
        await svc.stop()
    asyncio.run(run())


def test_web_volume_control_is_accepted_during_an_active_test(tmp_path):
    from fastapi.testclient import TestClient
    from web_server import create_web_app
    from continuous_service import ContinuousVoiceService
    from test_continuous_service import DevicePeer, Frontend, Robot, online

    class PendingService(ContinuousVoiceService):
        async def test_speaker(self):
            self.state['busy'] = True
            self.publish()
            await asyncio.Event().wait()

    svc = PendingService(Robot(), online, object(), Frontend, device_peer_factory=DevicePeer)
    with TestClient(create_web_app(svc, tmp_path), base_url='http://127.0.0.1') as client:
        with client.websocket_connect('ws://127.0.0.1/api/session', headers={'origin': 'http://127.0.0.1'}) as ws:
            assert ws.receive_json()['playbackVolume'] == 1
            ws.send_json({'type': 'speakerTest'})
            while not ws.receive_json()['busy']:
                pass
            ws.send_json({'type': 'playbackVolume', 'level': .5})
            while True:
                snapshot = ws.receive_json()
                if snapshot['playbackVolume'] == .5:
                    break
            assert snapshot['busy'] and not snapshot['notice']
            ws.send_json({'type': 'playbackVolume', 'level': '0.5'})
            while True:
                snapshot = ws.receive_json()
                if snapshot['error']:
                    break
            assert snapshot['playbackVolume'] == .5


def test_fixed_test_uses_the_same_attenuator_including_tail(monkeypatch):
    import continuous_service as module
    from test_continuous_service import DevicePeer
    pcm = b'\xe8\x03' * 977
    monkeypatch.setattr(module, 'load_test_audio', lambda: pcm)
    rendered = []

    class PlayingPeer(DevicePeer):
        async def start(self):
            self.playing = asyncio.create_task(self.play())
        async def play(self):
            while True:
                frame = await self.track.recv()
                rendered.extend(struct.unpack('<960h', bytes(frame.planes[0])))
        async def close(self):
            self.playing.cancel()
            await asyncio.gather(self.playing, return_exceptions=True)
            await super().close()

    async def run():
        svc, _, _ = service()
        svc.device_peer_factory = PlayingPeer
        await svc.set_playback_volume(.5)
        await svc.test_speaker()
        from playback_gain import PlaybackGain
        gain = PlaybackGain(sample_rate=48000)
        expected = gain.process(pcm) + gain.flush()
        expected_values = [round(v * .5) for (v,) in struct.iter_unpack('<h', expected)]
        assert rendered[:len(expected_values)] == expected_values
        assert not any(rendered[len(expected_values):])
        assert any(expected_values[-240:]), 'Nonzero flushed tail is compared too'
        assert 0 < max(map(abs, rendered)) <= 14500
        assert svc.snapshot()['playbackVolume'] == .5
        assert svc.state['speakerTest']['status'] == 'finished'
    asyncio.run(run())
