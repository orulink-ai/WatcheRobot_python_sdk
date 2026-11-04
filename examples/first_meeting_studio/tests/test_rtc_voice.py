import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.robot import SDKRobot

def test_current_firmware_uses_emotion_follow_without_obsolete_chat_gate():
    calls = []
    sdk = SimpleNamespace(
        supports=lambda cap: cap in {'expression.presets.authored.v1', 'expression.audio_follow.v2'},
        expression_runtime=SimpleNamespace(set_audio_follow=lambda enabled, **kw: calls.append((enabled, kw))))
    robot = SDKRobot(sdk, threading.Event(), rtc=object())
    async def run():
        await robot.expression('curious')
        await robot.expression('happy')
        await robot.expression('speaking')
    asyncio.run(run())
    assert calls == [(True, {'emotion': 0}), (True, {'emotion': 2}), (True, {'emotion': 0})]

def test_photo_retains_follow_canvas_without_default_flash(tmp_path):
    calls = []
    sdk = SimpleNamespace(supports=lambda cap: True,
        expression_runtime=SimpleNamespace(
            start=lambda preset: calls.append(('start', preset)),
            stop=lambda: calls.append(('stop',))),
        camera=SimpleNamespace(capture_with_feedback=lambda **kw: SimpleNamespace(save=lambda path: calls.append(('save',)))))
    robot = SDKRobot(sdk, threading.Event(), rtc=object())
    robot._follow_owned = True
    asyncio.run(robot.photo(tmp_path / 'photo.jpg'))
    assert calls == [('save',)]
    assert robot._follow_owned

def test_photo_rejects_old_firmware_before_touching_follow_canvas(tmp_path):
    import pytest
    from studio.robot import FirmwareCompatibilityError
    sdk = SimpleNamespace(supports=lambda cap: cap != 'camera.capture.retained_follow.v1')
    robot = SDKRobot(sdk, threading.Event(), rtc=object())
    robot._follow_owned = True
    with pytest.raises(FirmwareCompatibilityError, match='连续交接'):
        asyncio.run(robot.photo(tmp_path / 'photo.jpg'))
    assert robot._follow_owned

def test_thinking_returns_to_speaking_on_retained_canvas():
    calls = []
    sdk = SimpleNamespace(supports=lambda cap: True, expression_runtime=SimpleNamespace(
        set_audio_follow=lambda enabled, **kw: calls.append(('follow', kw)),
        start=lambda preset, **kw: calls.append(('start', preset)),
        update=lambda **kw: calls.append(('update', kw))))
    robot = SDKRobot(sdk, threading.Event(), rtc=object())
    async def run():
        await robot.expression('neutral')
        await robot.expression('thinking')
        await robot.expression('speaking')
        await robot.expression('listening')
    asyncio.run(run())
    assert calls == [('follow', {'emotion': 0}),
                     ('update', {'preset': 'processing'}),
                     ('follow', {'emotion': 0}),
                     ('update', {'preset': 'standby'})]

def test_voice_track_streams_actual_pcm_then_silence():
    from studio.rtc_voice import SpeechTrack
    async def run():
        track = SpeechTrack()
        done = track.enqueue(b'\x01\x00' * 480)
        frame = await track.recv()
        assert frame.sample_rate == 24000
        assert bytes(frame.planes[0])[:960] == b'\x01\x00' * 480
        assert not done.done()
        silent = await track.recv()
        assert done.done()
        assert bytes(silent.planes[0]) == bytes(960)
        track.stop()
    asyncio.run(run())

def test_rtc_start_failure_still_releases_uncertain_session():
    from studio.rtc_voice import RtcVoice
    calls = []
    def start(**kw):
        raise TimeoutError('lost start ack')
    rtc = SimpleNamespace(start=start, snapshot=lambda: {'active': True},
                          stop=lambda: calls.append('stop'))
    async def run():
        import pytest
        with pytest.raises(TimeoutError, match='lost start ack'):
            await RtcVoice(rtc, threading.Event()).play(bytes(960))
    asyncio.run(run())
    assert calls == ['stop']

def test_voice_pacing_does_not_burst_or_drop_pcm_after_stall(monkeypatch):
    from studio import rtc_voice
    clock = [100.0]
    waits = []
    async def sleep(delay):
        waits.append(delay)
        clock[0] += delay
    monkeypatch.setattr(rtc_voice.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(rtc_voice.asyncio, 'sleep', sleep)
    async def run():
        track = rtc_voice.SpeechTrack()
        pcm = b''.join(bytes([i, 0]) * 480 for i in range(8))
        track.enqueue(pcm)
        frames = [await track.recv()]
        clock[0] += .120
        frames.append(await track.recv())
        frames.append(await track.recv())
        assert waits[-1] >= .019
        assert b''.join(bytes(f.planes[0]) for f in frames) == pcm[:2880]
        assert [f.pts for f in frames] == [0, 480, 960]
        track.stop()
    asyncio.run(run())

def test_rtc_stop_failure_is_retained_for_cleanup_retry():
    from studio.rtc_voice import RtcVoice
    calls = []
    def stop():
        calls.append('stop')
        if len(calls) == 1:
            raise TimeoutError('stop ack')
    rtc = SimpleNamespace(snapshot=lambda: {'active': True}, stop=stop)
    async def run():
        import pytest
        voice = RtcVoice(rtc, threading.Event())
        with pytest.raises(TimeoutError, match='stop ack'):
            await voice.close()
        await voice.close()
    asyncio.run(run())
    assert calls == ['stop', 'stop']
