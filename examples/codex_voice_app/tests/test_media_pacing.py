import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from device_rtc_peer import DeviceAudioTrack
from media_pacing import MediaPacer, ArrivalCadence


class Clock:
    def __init__(self):
        self.now = 10.0
        self.waits = []

    async def sleep(self, delay):
        self.waits.append(delay)
        # Simulate asyncio waking early on a coarse Windows loop clock.
        self.now += max(delay / 2, .0001)


def test_media_clock_rechecks_early_wakeups_and_never_catches_up_in_bursts():
    async def run():
        clock = Clock()
        track = DeviceAudioTrack(clock=lambda: clock.now, sleep=clock.sleep)
        frames, times = [], []
        for i in range(6):
            if i == 2:
                clock.now += .15  # Event-loop stall, not lost PCM samples.
            track.push(b'\x01\x00' * 960, sample_rate=48000)
            frames.append(await track.recv())
            times.append(clock.now)
        assert times[1] >= times[0] + .02
        assert all(b - a >= .01 - 1e-9 for a, b in zip(times, times[1:]))
        assert times[3] >= times[2] + .02  # No burst following the stall.
        assert [f.pts for f in frames] == [0, 960, 1920, 2880, 3840, 4800]
        assert all(bytes(f.planes[0]) == b'\x01\x00' * 960 for f in frames)
        assert track.pacing.diagnostics()['intervalMaxMs'] >= 150
        track.stop()
    asyncio.run(run())


def test_idle_zeros_are_not_reported_as_partial_pcm_starvation():
    async def run():
        clock = Clock()
        track = DeviceAudioTrack(clock=lambda: clock.now, sleep=clock.sleep)
        await track.recv()
        track.push(b'\x01\x00' * 100, sample_rate=48000)
        await track.recv()
        assert track.partial_underflow_frames == 1
        assert track.empty_underflow_frames == 1
        track.stop()
    asyncio.run(run())


def test_ordinary_wakeup_jitter_does_not_accumulate_clock_drift():
    async def run():
        clock = Clock()
        async def oversleep(delay):
            clock.now += delay + .001
        pacer = MediaPacer(clock=lambda: clock.now, sleep=oversleep)
        start = clock.now
        for _ in range(501):
            await pacer.wait()
        assert clock.now - start == pytest.approx(10.001)
        assert pacer.diagnostics()['intervalMinMs'] >= 19
    asyncio.run(run())


def test_source_cadence_separates_arrival_gaps_from_media_timeline():
    cadence = ArrivalCadence()
    cadence.note(10, 0)
    cadence.note(10.02, .02)
    cadence.note(10.14, .04)
    data = cadence.diagnostics()
    assert data['intervalMaxMs'] == pytest.approx(120)
    assert data['arrivalMediaSkewMs'] == pytest.approx(100)
    assert data['intervalsOver40Ms'] == 1


def test_windows_media_wait_does_not_use_coarse_asyncio_timer(monkeypatch):
    import media_pacing as module
    calls = []
    monkeypatch.setattr(module.sys, 'platform', 'win32')
    async def thread_wait(callback, delay):
        calls.append((callback, delay))
    async def forbidden(delay):
        raise AssertionError('coarse asyncio timer must not pace Windows RTP')
    monkeypatch.setattr(module.asyncio, 'to_thread', thread_wait)
    monkeypatch.setattr(module.asyncio, 'sleep', forbidden)
    asyncio.run(module.media_sleep(.02))
    assert calls == [(module.time.sleep, .02)]


@pytest.mark.parametrize('delay', [.011, .015, .019])
def test_moderate_late_wakeups_keep_long_term_source_throughput(delay):
    async def run():
        clock = Clock()
        async def oversleep(wait):
            clock.now += wait + delay
        pacer = MediaPacer(clock=lambda: clock.now, sleep=oversleep)
        start = clock.now
        for _ in range(501):
            await pacer.wait()
        assert clock.now - start == pytest.approx(10 + delay)
        assert pacer.diagnostics()['intervalMinMs'] >= 10
    asyncio.run(run())


def test_alternating_wakeup_jitter_has_bounded_intervals_and_no_pcm_backlog():
    async def run():
        clock = Clock()
        next_source = clock.now + .02
        maximum_buffer = 0
        wakeups = 0
        async def sleep(wait):
            nonlocal next_source, maximum_buffer, wakeups
            clock.now += wait + (.015 if wakeups % 2 == 0 else 0)
            wakeups += 1
            while next_source <= clock.now + 1e-10:
                track.push(b'\x01\x00' * 960, sample_rate=48000)
                next_source += .02
            maximum_buffer = max(maximum_buffer, len(track.buffer))
        track = DeviceAudioTrack(clock=lambda: clock.now, sleep=sleep)
        track.push(b'\x01\x00' * 960, sample_rate=48000)
        start = clock.now
        for _ in range(501):
            frame = await track.recv()
            assert bytes(frame.planes[0]) == b'\x01\x00' * 960
        assert clock.now - start < 10.04
        assert maximum_buffer <= 5760
        assert track.pacing.diagnostics()['intervalMinMs'] >= 10
        track.stop()
    asyncio.run(run())
