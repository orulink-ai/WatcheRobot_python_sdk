"""Deterministic live playout regressions; no network, microphone or robot."""
import asyncio
from fractions import Fraction
import math
from pathlib import Path
import struct
import sys

import av
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from device_rtc_peer import DeviceAudioTrack
from playback_gain import PlaybackGain
from realtime_peer import RealtimePeer


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.now += delay


def pcm(value, count=960):
    return struct.pack('<h', value) * count


def read(frame):
    return bytes(frame.planes[0])[:frame.samples * 2]


def live_track():
    clock = Clock()
    track = DeviceAudioTrack(clock=clock, sleep=clock.sleep)
    track.configure_playout(prefill_ms=80, max_buffer_ms=600)
    return track, clock


def test_short_quiet_reply_has_useful_level_even_after_long_idle():
    gain = PlaybackGain(sample_rate=48000)
    gain.process(bytes(96000))
    source = [round(700 * math.sin(2 * math.pi * 310 * i / 48000)) for i in range(9600)]
    rendered = gain.process(struct.pack('<9600h', *source)) + gain.flush()
    samples = struct.unpack(f'<{len(rendered) // 2}h', rendered)[gain.lookahead:]
    rms = math.sqrt(sum(v * v for v in samples) / len(samples))
    assert rms >= 7000, 'A 200ms quiet reply must not spend most of its duration ramping up'
    assert max(map(abs, samples)) <= 29000


def test_live_prefill_masks_bounded_arrival_gap_without_losing_or_reordering_pcm():
    async def run():
        track, clock = live_track()
        output = []
        # Arrival at 0/20/40ms, then a 60ms gap and a three-frame catch-up.
        for tick in range(11):
            clock.now = tick * .02
            if tick < 3:
                track.push(pcm(tick + 1), sample_rate=48000)
            if tick == 5:
                for value in (4, 5, 6):
                    track.push(pcm(value), sample_rate=48000)
            output.append(read(await track.recv()))
        start = next(i for i, data in enumerate(output) if any(data))
        assert output[start:start + 6] == [pcm(v) for v in range(1, 7)]
        assert start == 4  # 80ms bounded additional downstream latency.
        assert all(data == bytes(1920) for data in output[:start])
        track.stop()
    asyncio.run(run())


def test_short_tail_releases_at_deadline_even_below_prefill_watermark():
    async def run():
        track, clock = live_track()
        track.push(pcm(500, 17), sample_rate=48000)
        frames = [await track.recv() for _ in range(5)]
        assert [f.pts for f in frames] == [0, 960, 1920, 2880, 3840]
        assert read(frames[-1]) == pcm(500, 17) + bytes(1920 - 34)
        assert not track.buffer
        assert clock.now == pytest.approx(.08)
        track.stop()
    asyncio.run(run())


def test_idle_silence_does_not_accumulate_and_stop_never_drains_old_audio():
    async def run():
        track, _ = live_track()
        for _ in range(200):
            track.push(bytes(1920), sample_rate=48000)
        assert not track.buffer
        track.push(pcm(731), sample_rate=48000)
        assert read(await track.recv()) == bytes(1920)
        track.stop()
        assert not track.buffer
        with pytest.raises(Exception):
            await track.recv()
    asyncio.run(run())


def test_live_overload_is_visible_before_old_audio_grows_to_seconds():
    track, _ = live_track()
    track.push(pcm(1, 48000 * 600 // 1000), sample_rate=48000)
    with pytest.raises(RuntimeError, match='backlog'):
        track.push(pcm(2), sample_rate=48000)
    assert not track.buffer, 'Live overload must latch failure and discard stale playback immediately'
    assert track.playout_diagnostics()['failed']
    track.stop()


def test_stale_playout_reports_failure_instead_of_silently_replaying_old_speech():
    async def run():
        failures = []
        clock = Clock()
        track = DeviceAudioTrack(clock=clock, sleep=clock.sleep, on_failure=failures.append)
        track.configure_playout(prefill_ms=80, max_buffer_ms=600)
        track.push(pcm(731), sample_rate=48000)
        clock.now = .7
        with pytest.raises(RuntimeError, match='stale'):
            await track.recv()
        assert len(failures) == 1
        assert not track.buffer
        assert track.playout_diagnostics()['failed']
        track.stop()
    asyncio.run(run())


def test_age_budget_includes_wait_before_the_playout_buffer():
    async def run():
        track, clock = live_track()
        clock.now = .55
        track.push(pcm(731), sample_rate=48000, received_at=0.0)
        clock.now = .61
        with pytest.raises(RuntimeError, match='stale'):
            await track.recv()
        assert not track.buffer
        track.stop()
    asyncio.run(run())


@pytest.mark.parametrize('config', [(-1, 600), (80, 80), (80, 2001), (True, 600)])
def test_bad_playout_config_does_not_mutate_state(config):
    track = DeviceAudioTrack()
    before = track.playout_diagnostics()
    with pytest.raises(ValueError):
        track.configure_playout(prefill_ms=config[0], max_buffer_ms=config[1])
    assert before == track.playout_diagnostics()
    track.stop()


def test_queued_pauses_and_partial_frames_are_preserved_exactly():
    async def run():
        track, _ = live_track()
        expected = pcm(731) + bytes(1920) + pcm(-731) + pcm(99, 17)
        for chunk in (expected[:100], expected[100:1998], expected[1998:]):
            track.push(chunk, sample_rate=48000)
        output = b''.join([read(await track.recv()) for _ in range(8)])
        beginning = output.index(pcm(731, 1))
        assert output[beginning:beginning + len(expected)] == expected
        assert not track.buffer
        track.stop()
    asyncio.run(run())


def test_repeated_scheduler_stalls_cannot_build_an_unbounded_delayed_voice_fifo():
    async def run():
        track, clock = live_track()
        queued = 0
        with pytest.raises(RuntimeError, match='backlog|stale'):
            for tick in range(1000):
                if tick and tick % 50 == 0:
                    clock.now += .15
                while queued * .02 <= clock.now:
                    track.push(pcm(731), sample_rate=48000)
                    queued += 1
                await track.recv()
        assert len(track.buffer) / 96 <= 600
        track.stop()
    asyncio.run(run())


def test_realtime_decoded_burst_yields_to_an_already_waiting_consumer():
    async def run():
        events = asyncio.Queue(maxsize=8)
        peer = RealtimePeer(events, realtime=True)
        class Source:
            index = 0
            async def recv(self):
                if self.index == 32:
                    raise asyncio.CancelledError()
                frame = av.AudioFrame(format='s16', layout='mono', samples=960)
                frame.sample_rate, frame.pts = 48000, self.index * 960
                frame.time_base = Fraction(1, 48000)
                frame.planes[0].update(pcm(731))
                self.index += 1
                return frame  # Deliberately never yields: decoded FIFO already ready.
        received = []
        async def consume():
            for _ in range(32):
                event = await events.get()
                assert event['method'] == 'thread/realtime/outputAudio/delta'
                received.append(event)
        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0)
        try:
            with pytest.raises(asyncio.CancelledError):
                await peer._receive(Source())
            await asyncio.wait_for(consumer, .5)
            assert len(received) == 32
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await peer.close()
    asyncio.run(run())
