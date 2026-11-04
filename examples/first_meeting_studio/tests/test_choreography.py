import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.scenes import Scene, DEFAULT_SCENES
from studio.service import MeetingService
from studio.config import Settings
from studio.robot import SDKRobot

def test_same_calm_preset_keeps_renderer_alive_and_stop_invalidates_cache():
    events = []
    sdk = SimpleNamespace(supports=lambda _: True,
        expression_runtime=SimpleNamespace(start=lambda preset: events.append(preset),
                                           stop=lambda: events.append('stop')),
        motion=SimpleNamespace(stop=lambda: None), audio=SimpleNamespace(stop=lambda: None))
    robot = SDKRobot(sdk, threading.Event())
    async def run():
        for name in ('curious', 'speaking', 'neutral', 'listening'):
            await robot.expression(name)
        await robot.stop()
        await robot.expression('listening')
    asyncio.run(run())
    assert events == ['neutral', 'stop', 'neutral']

def test_gesture_moves_remain_bounded_and_return_to_authored_pose(tmp_path):
    moves = []
    class Robot:
        async def expression(self, name): pass
        async def move(self, pan, tilt, duration): moves.append((pan, tilt, duration))
    service = MeetingService(Robot(), None, Settings(), tmp_path)
    scene = Scene(pan=110, tilt=100, gesture='nod', hold_seconds=0)
    asyncio.run(service.perform_scene(scene))
    assert len(moves) >= 3
    assert moves[-1][:2] == (110, 100)
    assert all(70 <= p <= 110 and 100 <= t <= 125 and d >= 600 for p,t,d in moves)
    assert len(set((p,t) for p,t,_ in moves)) > 1

def test_stop_during_gesture_prevents_remaining_moves(tmp_path):
    moves = []
    class Robot:
        async def expression(self, name): pass
        async def move(self, *pose):
            moves.append(pose)
            service.stopped.set()
    service = MeetingService(Robot(), None, Settings(), tmp_path)
    with pytest.raises(InterruptedError):
        asyncio.run(service.perform_scene(Scene(gesture='peek')))
    assert len(moves) == 1

def test_old_scene_remains_single_move_and_defaults_have_gestures():
    assert Scene.model_validate({'pan': 95}).gesture == 'none'
    assert sum(s.gesture != 'none' for s in DEFAULT_SCENES) >= 3

def test_failed_expression_start_is_retried_and_emotions_can_replay():
    events = []
    def start(preset):
        events.append(preset)
        if len(events) == 1: raise RuntimeError('transport unavailable')
    sdk = SimpleNamespace(supports=lambda _: True, expression_runtime=SimpleNamespace(start=start))
    robot = SDKRobot(sdk, threading.Event())
    async def run():
        with pytest.raises(RuntimeError): await robot.expression('neutral')
        await robot.expression('neutral')
        await robot.expression('warm')
        await robot.expression('warm')
        await robot.expression('neutral')
    asyncio.run(run())
    assert events == ['neutral', 'neutral', 'smile', 'smile', 'neutral']
