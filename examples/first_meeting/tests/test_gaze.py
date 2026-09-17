import asyncio

import pytest

from meeting.config import Settings
from meeting.gaze import gaze_plan, play_timeline


def test_direction_frames_and_servo_deadband():
    s = Settings()
    left = gaze_plan(s, 'left')
    right = gaze_plan(s, 'right')
    assert (left.animation, left.duration_ms) == ('standby3', 3600)
    assert (right.animation, right.duration_ms) == ('standby4', 2400)
    assert [(c.at_ms, c.pan, c.duration_ms) for c in left.cues] == [(200, 95, 200), (1300, 108, 700), (2900, 90, 500)]
    assert [(c.at_ms, c.pan, c.duration_ms) for c in right.cues] == [(100, 78, 400), (1700, 90, 500)]
    for plan in (left, right):
        pan = s.pan_center
        for cue in plan.cues:
            pulse = abs(cue.pan - pan) * 2000 / 180 / (cue.duration_ms / 20)
            assert 4 <= pulse <= 8.01
            pan = cue.pan


def test_timeline_uses_absolute_offsets_not_accumulated_roundtrips():
    async def run():
        now = [0.0]
        starts = []
        async def sleep(seconds):
            now[0] += seconds
        async def move(cue):
            starts.append(round(now[0] * 1000))
            now[0] += cue.duration_ms / 1000 + 0.09  # SDK acknowledgment delay
        await play_timeline(gaze_plan(Settings(), 'left'), move, sleep, lambda: now[0], lambda: None)
        assert starts == [200, 1300, 2900]
        assert now[0] == pytest.approx(3.6)
    asyncio.run(run())


def test_timeline_rejects_stale_movement_instead_of_playing_wrong_expression():
    async def run():
        now = [0.0]
        async def sleep(seconds):
            now[0] += seconds + 0.4
        async def move(cue):
            pytest.fail('Stale motion must not be sent')
        with pytest.raises(RuntimeError, match='滞后'):
            await play_timeline(gaze_plan(Settings(), 'left'), move, sleep, lambda: now[0], lambda: None)
    asyncio.run(run())
