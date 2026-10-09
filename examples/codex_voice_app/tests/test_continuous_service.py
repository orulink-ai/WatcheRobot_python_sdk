import asyncio
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from test_service import Agent, Robot, online
from continuous_service import ContinuousVoiceService
from device_rtc_peer import DeviceAudioTrack


class Frontend(Agent):
    voice_epoch = 1
    async def frontend_context(self, text, **kwargs):
        self.calls.append(('context', (text, kwargs)))
    async def frontend_text(self, text):
        self.calls.append(('frontend', text))


class DevicePeer:
    def __init__(self, rtc, events):
        self.closed = False
        self.output = []
        self.rates = []
        self.track = DeviceAudioTrack()
    async def start(self):
        pass
    async def append_audio(self, pcm, *, sample_rate=24000, received_at=None):
        self.output.append(pcm)
        self.rates.append(sample_rate)
        self.track.push(pcm, sample_rate=sample_rate, received_at=received_at)
    async def close(self):
        self.closed = True
        self.track.stop()
    def diagnostics(self):
        return {'connected': not self.closed}


def service():
    robot = Robot()
    robot.supports = lambda cap: cap == 'rtc.audio.full_duplex.v1'
    agent = Frontend()
    return ContinuousVoiceService(robot, online, object(), lambda: agent,
                                 device_peer_factory=DevicePeer), robot, agent


async def deliver(svc, agent, method, params):
    await agent.events.put(dict(method=method, params=params))
    for _ in range(10):
        await asyncio.sleep(.01)


def test_transcripts_do_not_submit_chitchat_as_backend_tasks():
    async def run():
        svc, robot, agent = service()
        await svc.start()
        await deliver(svc, agent, 'thread/realtime/transcript/done',
                      dict(role='user', text='你好', voiceEpoch=1))
        assert not any(c[0] == 'turn/start' for c in agent.calls)
        assert svc.state['messages'][-1]['content'] == '你好'
        assert robot.opens == 0
        await svc.stop()
        assert svc.state['stopStatus'] == 'confirmed'
    asyncio.run(run())


def test_live_audio_and_microphone_keep_flowing_during_backend_work():
    async def run():
        svc, robot, agent = service()
        await svc.start()
        await deliver(svc, agent, 'local/delegation', dict(id='d', text='转头', voiceEpoch=1))
        assert any(c[0] == 'turn/start' for c in agent.calls)
        pcm = b'\x01\x00' * 320
        await deliver(svc, agent, 'local/deviceAudio', dict(audio={'data': base64.b64encode(pcm).decode()}))
        await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
                      dict(voiceEpoch=1, audio={'data': base64.b64encode(pcm).decode(),
                          'sampleRate': 48000, 'numChannels': 1, 'samplesPerChannel': 320}))
        assert svc.state['agentWorking'] and svc.snapshot()['microphone']
        assert svc.device_peer.rates == [48000]
        assert svc.device_peer.output[0][:480] == bytes(480)
        assert len(svc.device_peer.output[0]) == len(pcm)
        assert any(c[0] == 'thread/realtime/appendAudio' for c in agent.calls)
        assert not robot.played and not robot.opens
        await svc.stop()
    asyncio.run(run())


def test_realtime_playback_compensates_quiet_source_before_device_encoding():
    async def run():
        import struct
        svc, robot, agent = service()
        await svc.start()
        pcm = struct.pack('<h', 1000) * 960
        await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
                      dict(voiceEpoch=1, audio={'data': base64.b64encode(pcm).decode(),
                           'sampleRate': 48000, 'numChannels': 1, 'samplesPerChannel': 960}))
        output = svc.device_peer.output[0]
        assert 4000 < max(struct.unpack('<960h', output)) < 7000
        assert svc.device_peer.rates == [48000]
        assert svc.snapshot()['playbackGain']['inputPeak'] == 1000
        assert svc.snapshot()['playbackGain']['profile'] == 'clear-speech-v4'
        assert svc.device_peer.track.playout_diagnostics()['prefillMs'] == 80
        await svc.stop()
    asyncio.run(run())


def test_stale_output_is_rejected_before_gain_changes_or_audio_admission():
    async def run():
        import time
        svc, _, agent = service()
        await svc.start()
        peer = svc.device_peer
        before = svc.playback_gain.diagnostics()
        await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
            dict(voiceEpoch=1, receivedAtPerf=time.perf_counter() - .7,
                 audio=dict(data=base64.b64encode(b'\xe8\x03' * 960).decode(),
                            sampleRate=48000, numChannels=1, samplesPerChannel=960)))
        assert svc.playback_gain.diagnostics() == before
        assert not peer.output and not peer.track.buffer
        assert svc.state['error'] and not svc.state['connected']
        await svc.stop()
    asyncio.run(run())


def test_startup_stale_idle_advances_dsp_without_failing_the_new_conversation():
    async def run():
        import time
        svc, _, agent = service()
        await svc.start()
        await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
            dict(voiceEpoch=1, receivedAtPerf=time.perf_counter() - 2,
                 audio=dict(data=base64.b64encode(bytes(1920)).decode(),
                            sampleRate=48000, numChannels=1, samplesPerChannel=960)))
        assert svc.state['connected'] and not svc.state['error']
        assert svc.playback_gain.samples == 960
        assert not svc.device_peer.track.buffer and not svc.device_peer.output
        assert svc.media_forwarding['staleIdleSkippedMs'] == 20
        await svc.stop()
    asyncio.run(run())


def test_exact_idle_cannot_fail_when_age_crosses_deadline_between_clock_reads(monkeypatch):
    import continuous_service as module
    async def run():
        svc, _, agent = service()
        await svc.start()
        # First reads are below 600ms; later validation would cross it.
        calls = [0]
        def boundary_clock():
            calls[0] += 1
            return 100.599 if calls[0] < 3 else 100.601
        with monkeypatch.context() as patch:
            patch.setattr(module.time, 'perf_counter', boundary_clock)
            svc.device_peer.track.playout.clock = boundary_clock
            await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
                dict(voiceEpoch=1, receivedAtPerf=100.0,
                    audio=dict(data=base64.b64encode(bytes(1920)).decode(),
                               sampleRate=48000, numChannels=1, samplesPerChannel=960)))
        assert svc.state['connected'] and not svc.state['error']
        assert not svc.device_peer.track.buffer
        assert svc.playback_gain.samples == 960
        await svc.stop()
    asyncio.run(run())


@pytest.mark.parametrize('barrier', ['queued', 'failed'])
def test_zero_pcm_cannot_bypass_age_when_program_is_queued_or_playout_failed(barrier):
    import time
    async def run():
        svc, _, agent = service()
        await svc.start()
        peer = svc.device_peer
        before = svc.playback_gain.diagnostics()
        if barrier == 'queued':
            peer.track.push(b'\xe8\x03' * 960, sample_rate=48000)
        else:
            peer.track.playout.failed = True
        await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
            dict(voiceEpoch=1, receivedAtPerf=time.perf_counter() - .7,
                 audio=dict(data=base64.b64encode(bytes(1920)).decode(),
                            sampleRate=48000, numChannels=1, samplesPerChannel=960)))
        assert not svc.state['connected'] and svc.state['error']
        assert svc.playback_gain.diagnostics() == before
        assert not peer.output
        await svc.stop()
    asyncio.run(run())


def test_stale_idle_exception_does_not_discard_pending_dsp_speech_tail():
    async def run():
        import time
        svc, _, agent = service()
        await svc.start()
        svc.playback_gain.process(b'\xe8\x03' * 17)
        assert svc.playback_gain.has_pending_audio()
        before = svc.playback_gain.diagnostics()
        peer = svc.device_peer
        await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
            dict(voiceEpoch=1, receivedAtPerf=time.perf_counter() - 2,
                 audio=dict(data=base64.b64encode(bytes(1920)).decode(),
                            sampleRate=48000, numChannels=1, samplesPerChannel=960)))
        assert svc.state['error'] and not svc.state['connected']
        assert svc.playback_gain.diagnostics() == before
        assert not peer.output
        await svc.stop()
    asyncio.run(run())


def test_stop_fences_local_playout_before_slow_physical_release():
    async def run():
        svc, _, _ = service()
        await svc.start()
        peer = svc.device_peer
        peer.track.push(b'\xe8\x03' * 960, sample_rate=48000)
        entered, release = asyncio.Event(), asyncio.Event()
        original = svc._physical_stop
        async def slow_stop():
            entered.set()
            await release.wait()
            return await original()
        svc._physical_stop = slow_stop
        stopping = asyncio.create_task(svc.stop())
        await asyncio.wait_for(entered.wait(), 1)
        try:
            assert peer.track.readyState == 'ended'
            assert not peer.track.buffer
        finally:
            release.set()
            await stopping
        assert svc.state['stopStatus'] == 'confirmed'
    asyncio.run(run())


def test_delegation_is_deduplicated_but_busy_frontend_can_still_talk():
    async def run():
        svc, _, agent = service()
        await svc.start()
        for identity in ['d', 'd', 'e']:
            await deliver(svc, agent, 'local/delegation', dict(id=identity, text='看看', voiceEpoch=1))
        assert len([c for c in agent.calls if c[0] == 'turn/start']) == 1
        assert any(c[0] == 'context' and c[1][1]['delegation_id'] == 'e' for c in agent.calls)
        await svc.text('你正在做什么？')
        assert ('frontend', '你正在做什么？') in agent.calls
        await svc.stop()
    asyncio.run(run())


def test_repeated_start_preserves_live_delegation_and_deduplication():
    async def run():
        svc, _, agent = service()
        await svc.start()
        await deliver(svc, agent, 'local/delegation', dict(id='d', text='看前面', voiceEpoch=1))
        peer = svc.device_peer
        await svc.start()
        assert svc.delegation_id == 'd' and svc.handoffs == {'d'}
        assert svc.device_peer is peer and svc.state['agentWorking']
        await deliver(svc, agent, 'local/delegation', dict(id='d', text='看前面', voiceEpoch=1))
        assert len([c for c in agent.calls if c[0] == 'turn/start']) == 1
        await svc.stop()
    asyncio.run(run())


def test_backend_result_returns_to_same_frontend_without_preparing_new_rtc():
    async def run():
        svc, _, agent = service()
        await svc.start()
        await deliver(svc, agent, 'local/delegation', dict(id='d', text='看前面', voiceEpoch=1))
        await deliver(svc, agent, 'turn/started', dict(turn={'id': 't'}))
        await deliver(svc, agent, 'turn/completed', dict(turn={'id': 't', 'status': 'completed',
            'items': [{'type': 'agentMessage', 'text': '看到桌子', 'phase': 'final_answer'}]}))
        assert any(c[0] == 'context' and c[1][1] == {'delegation_id': 'd', 'channel': 'speakable'}
                   and '看到桌子' in c[1][0] for c in agent.calls)
        assert not svc.state['agentWorking'] and svc.mic
        await svc.stop()
    asyncio.run(run())


def test_slow_backend_context_handoff_does_not_block_live_microphone():
    async def run():
        svc, _, agent = service()
        await svc.start()
        await deliver(svc, agent, 'local/delegation', dict(id='d', text='看前面', voiceEpoch=1))
        await deliver(svc, agent, 'turn/started', dict(turn={'id': 't'}))
        entered, release = asyncio.Event(), asyncio.Event()
        original = agent.frontend_context
        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            await original(*args, **kwargs)
        agent.frontend_context = delayed
        try:
            await agent.events.put(dict(method='turn/completed', params={'turn': {
                'id': 't', 'status': 'completed', 'items': [{'type': 'agentMessage', 'text': '桌子'}]}}))
            await asyncio.wait_for(entered.wait(), 1)
            await deliver(svc, agent, 'local/deviceAudio', dict(audio={'data': base64.b64encode(b'\x00\x00' * 320).decode()}))
            assert svc.state['micFrames'] == 1
            assert svc.state['agentWorking'], 'Admission remains owned until handoff finishes'
        finally:
            release.set()
            await svc.stop()
    asyncio.run(run())


def test_missing_firmware_rtc_capability_fails_closed_without_legacy_fallback():
    async def run():
        svc, robot, _ = service()
        robot.supports = lambda _: False
        with pytest.raises(RuntimeError, match='rtc.audio.full_duplex'):
            await svc.start()
        assert not robot.opens and not robot.played
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'voiceEpoch': None}, {'voiceEpoch': True}, {'sampleRate': 24000},
    {'numChannels': 2}, {'samplesPerChannel': None}, {'samplesPerChannel': 959},
])
def test_incomplete_native_output_fails_before_dsp_or_queue_mutation(change):
    async def run():
        svc, _, agent = service()
        svc.agent = agent
        svc.device_peer = DevicePeer(None, agent.events)
        audio = dict(data=base64.b64encode(bytes(1920)).decode(), sampleRate=48000,
                     numChannels=1, samplesPerChannel=960)
        params = dict(voiceEpoch=1, audio=audio)
        for key, value in change.items():
            (params if key == 'voiceEpoch' else audio)[key] = value
        before = svc.playback_gain.diagnostics()
        await agent.events.put(dict(method='thread/realtime/outputAudio/delta', params=params))
        with pytest.raises(ValueError):
            await asyncio.wait_for(svc._events(), .5)
        assert before == svc.playback_gain.diagnostics()
        assert not svc.device_peer.output
    asyncio.run(run())


def test_native_idle_pcm_advances_dsp_without_indicating_a_reply():
    async def run():
        svc, _, agent = service()
        await svc.start()
        await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
            dict(voiceEpoch=1, audio=dict(data=base64.b64encode(bytes(1920)).decode(),
                sampleRate=48000, numChannels=1, samplesPerChannel=960)))
        assert svc.playback_gain.samples == 960
        assert svc.device_peer.rates == []  # Paced track already supplies idle zeros.
        assert not svc.device_peer.track.buffer
        assert not svc.state['speaking'] and not svc.state['outputBytes']
        await svc.stop()
    asyncio.run(run())


def test_startup_idle_burst_advances_state_without_building_a_speaker_fifo():
    async def run():
        svc, _, agent = service()
        await svc.start()
        for _ in range(3):
            await deliver(svc, agent, 'thread/realtime/outputAudio/delta',
                dict(voiceEpoch=1, audio=dict(data=base64.b64encode(bytes(96000)).decode(),
                    sampleRate=48000, numChannels=1, samplesPerChannel=48000)))
        async def drained():
            while svc.playback_gain.samples < 144000:
                await asyncio.sleep(.01)
        await asyncio.wait_for(drained(), 3)
        assert svc.playback_gain.samples == 144000
        assert not svc.device_peer.track.buffer
        assert not svc.state['outputBytes'] and not svc.state['speaking']
        await svc.stop()
    asyncio.run(run())


def test_large_native_event_yields_for_stop_between_20ms_slices():
    async def run():
        svc, _, agent = service()
        svc.agent = agent
        peer = svc.device_peer = DevicePeer(None, agent.events)
        original = peer.append_audio
        async def append(pcm, **kwargs):
            await original(pcm, **kwargs)
            if len(peer.output) == 2:
                svc.stopping = True
        peer.append_audio = append
        await agent.events.put(dict(method='thread/realtime/outputAudio/delta',
            params=dict(voiceEpoch=1, audio=dict(data=base64.b64encode(b'\xe8\x03' * 48000).decode(),
                sampleRate=48000, numChannels=1, samplesPerChannel=48000))))
        await asyncio.wait_for(svc._events(), .5)
        assert len(peer.output) == 2
        assert svc.playback_gain.samples == 1920
        assert not svc.state['outputBytes']
    asyncio.run(run())


def test_stop_at_last_native_slice_does_not_revive_speaking_metrics():
    async def run():
        svc, _, agent = service()
        svc.agent = agent
        peer = svc.device_peer = DevicePeer(None, agent.events)
        original = peer.append_audio
        async def append(pcm, **kwargs):
            await original(pcm, **kwargs)
            svc.stopping = True
        peer.append_audio = append
        await agent.events.put(dict(method='thread/realtime/outputAudio/delta',
            params=dict(voiceEpoch=1, audio=dict(data=base64.b64encode(b'\xe8\x03' * 960).decode(),
                sampleRate=48000, numChannels=1, samplesPerChannel=960))))
        await asyncio.wait_for(svc._events(), .5)
        assert len(peer.output) == 1
        assert not svc.state['speaking'] and not svc.state['outputBytes']
    asyncio.run(run())
