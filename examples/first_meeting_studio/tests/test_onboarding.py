import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.onboarding import Onboarding, is_wake_touch


def test_only_screen_tap_inside_visible_invitation_wakes():
    def event(x=206, y=206, source='screen_touch'):
        return SimpleNamespace(source=source, action='tap', x=x, y=y)
    assert is_wake_touch(event())
    assert not is_wake_touch(event(0, 0))
    assert not is_wake_touch(event(410, 206))
    assert not is_wake_touch(event(source='back_touch'))


class Robot:
    def __init__(self):
        self.frames = []
        self.tapped = False
        self.cleared = False
    async def onboarding_screen(self, stage): self.frames.append(stage)
    async def onboarding_breathe(self, expanded): pass
    async def clear_wake_inputs(self): self.cleared = True
    async def wait_wake_touch(self):
        await asyncio.sleep(0)
        return self.tapped
    async def finish_invitation(self): self.frames.append('handoff')


def test_stage_order_repeated_requests_and_automatic_wake():
    async def scenario():
        flow, robot = Onboarding(), Robot()
        flow.reset()
        async def pause(_): await asyncio.sleep(0)
        task = asyncio.create_task(flow.run(robot, lambda: None, pause, lambda *_: None))
        while not flow.snapshot()['can_advance']: await asyncio.sleep(0)
        ticket = flow.snapshot()
        flow.advance(ticket['run_id'], 'scan')
        with pytest.raises(ValueError): flow.advance(ticket['run_id'], 'scan')
        while flow.stage != 'network' or not flow.snapshot()['can_advance']: await asyncio.sleep(0)
        with pytest.raises(ValueError): flow.advance('old-session', 'network')
        flow.advance(ticket['run_id'], 'network')
        await asyncio.wait_for(task, 1)
        assert flow.stage == 'awakening'
        assert robot.frames == ['scan', 'network', 'handoff']
    asyncio.run(scenario())


def test_stop_during_simulation_never_opens_microphone_or_wakes():
    async def scenario():
        flow, robot = Onboarding(), Robot()
        flow.reset()
        stopped = False
        def check():
            if stopped: raise InterruptedError('stop')
        async def pause(_):
            nonlocal stopped
            stopped = True
            await asyncio.sleep(0)
        with pytest.raises(InterruptedError):
            await flow.run(robot, check, pause, lambda *_: None)
        assert robot.frames == ['scan']
        assert not flow.snapshot()['can_advance']
        assert flow.stage == 'interrupted'
    asyncio.run(scenario())


def test_screen_failure_is_not_an_advanceable_stage():
    class Broken(Robot):
        async def onboarding_screen(self, stage): raise TimeoutError('device offline')
    async def scenario():
        flow = Onboarding()
        flow.reset()
        with pytest.raises(TimeoutError):
            await flow.run(Broken(), lambda: None, asyncio.sleep, lambda *_: None)
        assert flow.stage == 'error'
        assert not flow.snapshot()['can_advance']
    asyncio.run(scenario())

@pytest.mark.parametrize('with_scan', [False, True])
def test_connection_expressions_sound_and_closed_handoff(with_scan):
    import threading
    from studio.robot import SDKRobot
    events = []
    class Expressions:
        def set_audio_follow(self, enabled, **params): events.append(('follow', params))
        def start(self, preset, **params): events.append(('preset', preset))
        def update(self, **params): events.append(('update', params))
    class Job:
        def wait(self, timeout): events.append(('sound_done', None))
    class Audio:
        def play(self, name): events.append(('sound', name)); return Job()
        def stop(self): pass
    sdk = SimpleNamespace(supports=lambda _: True, expression_runtime=Expressions(), audio=Audio())
    robot = SDKRobot(sdk, threading.Event(), rtc=object())
    async def run():
        async def pause(_): pass
        robot._transition_pause = pause
        if with_scan:
            await robot.onboarding_screen('scan')
        await robot.onboarding_screen('network')
        await robot.finish_invitation()
        before = len(events)
        await robot.expression('sleepy')
        assert len(events) == before
    asyncio.run(run())
    assert not any(kind == 'preset' for kind, _ in events)
    assert not any(kind == 'follow' and value.get('emotion') == 2 for kind, value in events)
    updates = [value for kind, value in events if kind == 'update']
    assert any(v.get('accessory') == 'provision_network' for v in updates)
    assert all(v.get('custom_accessory_layer') == 'front' for v in updates if 'accessory' in v)
    assert any(v.get('accessory_opacity') == 0 and v.get('transition_ms') == 1200 for v in updates)
    assert sum(e == ('sound', 'wifi_connected') for e in events) == 1
    fade_index = next(i for i, (k, v) in enumerate(events) if k == 'update' and v.get('accessory_opacity') == 0)
    assert next(i for i, (k, v) in enumerate(events) if k == 'sound_done') < fade_index
    final = [v for k, v in events if k == 'update'][-1]
    assert final['mouth_opacity'] == 0
    assert final['left_upper_lid_y'] == -32
    assert final['accessory'] == 'none'


def test_completed_device_skips_gates_but_rehearsal_restores_them(tmp_path):
    from studio.config import Settings
    from studio.service import MeetingService
    class IdleRobot(Robot):
        async def stop(self): pass
        async def expression(self, name): pass
    async def scenario():
        service = MeetingService(IdleRobot(), None, Settings(voice_enabled=False, face_tracking_enabled=False), tmp_path)
        service.device_id = 'device-a'
        service.memory.complete('device-a', '小明', '', False)
        service.start(onboarding=True)
        assert service.onboarding.stage == 'skipped'
        assert not service.include_onboarding
        await service.stop()
        service.start(onboarding=True, rehearsal=True)
        assert service.include_onboarding
        assert service.onboarding.stage == 'preparing'
        await service.stop()
        assert service.memory.get('device-a')['name'] == '小明'
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['network', 'awakening'])
def test_stop_in_later_gates_never_hands_off(stage):
    async def scenario():
        flow, robot = Onboarding(), Robot()
        flow.reset()
        def check():
            if flow.stage == stage: raise InterruptedError('stop')
        async def pause(_):
            if flow.snapshot()['can_advance']:
                flow.advance(flow.run_id, flow.stage)
            await asyncio.sleep(0)
        with pytest.raises(InterruptedError):
            await flow.run(robot, check, pause, lambda *_: None)
        assert flow.stage == 'interrupted'
        assert 'handoff' not in robot.frames
        assert not flow.ready
    asyncio.run(scenario())


def test_connection_probe_is_silent_finite_and_does_not_complete_memory(tmp_path):
    from studio.config import Settings
    from studio.service import MeetingService
    class ProbeRobot(Robot):
        async def move(self, *args): self.frames.append('move')
        async def eyelids(self, **kwargs): self.frames.append('lids')
        async def expression(self, name): self.frames.append(name)
        async def stop(self): self.frames.append('stop')
    async def scenario():
        robot = ProbeRobot()
        service = MeetingService(robot, None, Settings(), tmp_path)
        service.device_id = 'probe-device'
        async def pause(_): await asyncio.sleep(0)
        service.pause = pause
        service.start(connection_probe=True)
        await asyncio.wait_for(service.task, 2)
        assert service.phase == 'stopped'
        assert robot.frames.index('network') < robot.frames.index('handoff') < robot.frames.index('sleepy')
        assert robot.frames[-1] == 'stop'
        assert not service.memory.get('probe-device')
    asyncio.run(scenario())


def test_failure_retry_and_invalid_action_do_not_skip_password():
    async def scenario():
        flow, robot = Onboarding(), Robot()
        flow.reset()
        async def pause(_): await asyncio.sleep(0)
        task = asyncio.create_task(flow.run(robot, lambda: None, pause, lambda *_: None))
        async def ready(stage):
            async def wait():
                while flow.stage != stage or not flow.ready: await asyncio.sleep(0)
            await asyncio.wait_for(wait(), 1)
        await ready('scan')
        with pytest.raises(ValueError): flow.advance(flow.run_id, 'scan', 'retry')
        with pytest.raises(ValueError): flow.advance(flow.run_id, 'scan', 'fail')
        flow.advance(flow.run_id, 'scan')
        await ready('network')
        flow.advance(flow.run_id, 'network', 'fail')
        await ready('failed')
        with pytest.raises(ValueError): flow.advance(flow.run_id, 'failed', 'retry')
        flow.advance(flow.run_id, 'failed', 'password')
        await ready('network')
        flow.advance(flow.run_id, 'network')
        await asyncio.wait_for(task, 1)
        assert robot.frames == ['scan', 'network', 'failed', 'network', 'handoff']
    asyncio.run(scenario())


def test_stale_revision_cannot_advance_revisited_stage():
    flow = Onboarding()
    flow.reset()
    flow.stage, flow.ready = 'scan', True
    old = flow.snapshot()['revision']
    flow.revision += 2
    with pytest.raises(ValueError):
        flow.advance(flow.run_id, 'scan', revision=old)
    assert flow.ready
    flow.advance(flow.run_id, 'scan', revision=flow.revision)
    assert not flow.ready


@pytest.mark.parametrize('stage', [[], {}, None])
def test_invalid_stage_is_controlled_error(stage):
    with pytest.raises(ValueError):
        Onboarding().advance('', stage)
