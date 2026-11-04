import asyncio
import sys
import inspect
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.config import Settings
from studio.scenes import Scene
from studio.service import MeetingService
from studio.waking import WAKE_POSES
from watcherobot.robot import ExpressionRuntimeDomain


def test_waking_parameters_match_public_sdk_contract():
    signature = inspect.signature(ExpressionRuntimeDomain.update)
    for pose in WAKE_POSES:
        signature.bind(None, **pose.lids())
        assert 0 <= pose.lids()['transition_ms'] <= 2000


def test_waking_slow_open_and_mouth_reveal():
    positions = [p.lids()['gaze_y'] for p in WAKE_POSES]
    assert positions[0] == 1.0
    assert positions[1] >= .9
    assert WAKE_POSES[1].hold_ms >= 150
    assert WAKE_POSES[0].tilt == 100
    assert positions[-1] == 0
    assert all(0 <= p <= 1 for p in positions)
    values = [p.lids()['mouth_opacity'] for p in WAKE_POSES]
    assert values[0] == 0
    assert values[-1] == 1
    assert all(a <= b for a, b in zip(values, values[1:]))
    assert any(0 < v < 1 for v in values)
    assert WAKE_POSES[-2].duration_ms >= 1800
    assert sum(p.duration_ms for p in WAKE_POSES) >= 6500
    assert sum(p.duration_ms + p.hold_ms for p in WAKE_POSES) == 8000


def test_two_openings_with_one_relapse_and_slower_timing():
    assert len(WAKE_POSES) == 5
    closed, peek, relapse, lifting, awake = WAKE_POSES
    for pose in (closed, relapse):
        assert (pose.left_upper, pose.right_upper, pose.lower) == (-32, -32, 32)
    assert lifting.left_upper < peek.left_upper < closed.left_upper
    assert lifting.lower > peek.lower > closed.lower
    assert peek.left_upper != peek.right_upper
    assert 600 < relapse.duration_ms < peek.duration_ms
    assert peek.duration_ms >= 1800
    assert lifting.duration_ms >= 2000
    assert relapse.tilt < peek.tilt
    assert relapse.gaze_y >= peek.gaze_y
    assert all(p.mouth_opacity == 0 for p in WAKE_POSES[:3])
    assert lifting.gaze_y < relapse.gaze_y
    assert lifting.tilt < awake.tilt


def test_sleepy_coordinates_lids_and_head_then_restores_blink(tmp_path):
    events = []
    class Robot:
        async def expression(self, name): events.append(('expression', name))
        async def eyelids(self, **params): events.append(('lids', params))
        async def move(self, p, t, d): events.append(('move', (p, t, d)))
    service = MeetingService(Robot(), None, Settings(), tmp_path)
    async def pause(_): pass
    service.pause = pause
    asyncio.run(service.perform_scene(Scene(expression='sleepy', hold_seconds=0)))
    lids = [v for k, v in events if k == 'lids']
    moves = [v for k, v in events if k == 'move']
    assert len(lids) >= 5
    assert lids[0]['left_upper_lid_y'] == -32
    assert lids[0]['auto_blink'] is False
    assert lids[-1]['auto_blink'] is True
    assert lids[-1]['left_upper_lid_y'] == -80
    assert all(70 <= p <= 110 and 100 <= t <= 125 and d >= 600 for p, t, d in moves)
    assert moves[0][1] < moves[-1][1]
    assert any(x['left_upper_lid_y'] != x['right_upper_lid_y'] for x in lids)


def test_stop_during_waking_prevents_further_device_commands(tmp_path):
    events = []
    class Robot:
        async def expression(self, name): pass
        async def eyelids(self, **params): events.append('lids')
        async def move(self, *args):
            events.append('move')
            service.stopped.set()
    service = MeetingService(Robot(), None, Settings(), tmp_path)
    with pytest.raises(InterruptedError):
        asyncio.run(service.perform_scene(Scene(expression='sleepy')))
    assert events == ['lids', 'move']
