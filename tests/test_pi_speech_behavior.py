"""Behavior ownership, bounded local actions and expression timing stay independent of speech."""
import asyncio
import importlib.util
import sys
from functools import wraps
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'examples' / 'joyinside_pi'


def module():
    spec = importlib.util.spec_from_file_location('pi_speech_behavior', ROOT / 'behavior.py')
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def run_async(function):
    @wraps(function)
    def run():
        return asyncio.run(function())
    return run


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Robot:
    def __init__(self, capabilities=None):
        self.device_info = {'device_id': 'robot-A'}
        self.capabilities = set(capabilities if capabilities is not None else (
            'motion', 'motion.action.once.v1', 'expression.audio_follow.v1', 'expression.audio_follow.v2'))
        self.calls = []
        self.jobs = []
        self.fail_disable = False
        self.motion = SimpleNamespace(play_action=self.play, move_to=self.move, stop=self.stop_motion)
        self.expression_runtime = SimpleNamespace(set_audio_follow=self.follow)

    def follow(self, enabled, *, emotion=None):
        self.calls.append(('expression', enabled, emotion))
        if not enabled and self.fail_disable:
            raise RuntimeError('private device detail')

    def move(self, **kwargs):
        self.calls.append(('home', kwargs))
        job = SimpleNamespace(state='completed')
        self.jobs.append(job)
        return job

    def play(self, action_id):
        self.calls.append(('action', action_id))
        job = SimpleNamespace(state='completed')
        self.jobs.append(job)
        return job

    def stop_motion(self):
        self.calls.append(('stop_motion',))


async def until(predicate):
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(.003)
    raise AssertionError('background operation did not complete')


def test_explicit_expression_is_contextual_and_respects_negation():
    policy = module().ExpressionPolicy()
    assert policy.infer('谢谢你，太好了！') == 2
    policy.user_cue('请展示生气的表情', 0)
    assert policy.resolve(1, 1) == 3
    assert policy.resolve(4.01, 1) == 1
    policy.user_cue('不要展示生气的表情，我只是说有点难过', 5)
    assert policy.resolve(5.1, 1) == 1
    policy.user_cue('不要生气，请做开心的表情', 6)
    assert policy.resolve(6.1, 0) == 2
    policy.user_cue('恢复正常表情', 7)
    assert policy.resolve(7.1, 3) == 0


def test_resource_contract_matches_seven_firmware_snapshots():
    behavior = module()
    firmware = Path(__file__).resolve().parents[2] / 'WatcheRobot_esp32' / 'firmware' / 's3' / 'spiffs' / 'actions'
    import json
    assert len(behavior.ACTIONS) == 7
    for spec in behavior.ACTIONS.values():
        if not firmware.exists():
            continue  # SDK standalone checkout need not contain the firmware submodule.
        action = json.loads((firmware / (spec.action_id + '.json')).read_text())
        assert spec.duration_ms == (action['frame_end'] + 1) * 20
        points = [obj['keyframe_data'] for obj in action['animated_objects']]
        assert tuple(axis[0]['rotation_angle'] for axis in points) == (90, 108)
        assert tuple(axis[-1]['rotation_angle'] for axis in points) == (90, 108)


@run_async
async def test_expression_and_phase_dont_wait_for_inflight_motion_and_neutral_repeats():
    behavior = module()
    robot, clock = Robot(), Clock()
    owner = behavior.RobotBehavior(robot, clock=clock)
    await owner.start()
    await owner.set_phase('speaking', text='这是普通的一段话。')
    await until(lambda: ('action', 'chat_v1_talk') in robot.calls)
    first = robot.calls.count(('action', 'chat_v1_talk'))
    await owner.set_phase('thinking')
    await until(lambda: ('expression', True, 4) in robot.calls)
    assert robot.calls.count(('action', 'chat_v1_talk')) == first
    await owner.set_phase('speaking', text='这是普通的一段话。')
    clock.advance(3)
    await asyncio.sleep(.05)
    clock.advance(.5)
    await until(lambda: robot.calls.count(('action', 'chat_v1_talk')) == first + 1)
    await owner.stop()
    assert robot.calls[-1] == ('expression', False, None) or ('expression', False, None) in robot.calls
    assert not owner.snapshot()['cleanup_pending']


@run_async
async def test_motion_coalesces_latest_emotion_without_queue_or_repeat():
    behavior = module()
    robot, clock = Robot(), Clock()
    owner = behavior.RobotBehavior(robot, clock=clock)
    await owner.start()
    await owner.set_phase('speaking', text='我很开心')
    await until(lambda: ('action', 'chat_v1_happy') in robot.calls)
    await owner.set_phase('speaking', text='有点伤心')
    await until(lambda: ('expression', True, 5) in robot.calls)
    await owner.set_phase('speaking', text='有点无奈')
    await until(lambda: ('expression', True, 6) in robot.calls)
    clock.advance(3)
    await until(lambda: ('action', 'chat_v1_resigned') in robot.calls)
    assert ('action', 'chat_v1_sad') not in robot.calls
    clock.advance(10)
    await asyncio.sleep(.08)
    assert robot.calls.count(('action', 'chat_v1_resigned')) == 1
    await owner.stop()


@run_async
async def test_capabilities_degrade_independently_and_never_break_speech():
    behavior = module()
    robot = Robot({'expression.audio_follow.v1', 'expression.audio_follow.v2'})
    owner = behavior.RobotBehavior(robot)
    await owner.start()
    await owner.set_phase('thinking')
    await until(lambda: ('expression', True, 4) in robot.calls)
    state = owner.snapshot()
    assert state['degraded'] and state['expression_available'] and not state['motion_available']
    await owner.stop()
    robot = Robot(set())
    owner = behavior.RobotBehavior(robot)
    await owner.start()
    await owner.set_phase('speaking', text='你好')
    await owner.stop()
    assert not robot.calls
    assert owner.snapshot()['degraded']


@run_async
async def test_cleanup_failure_retains_owner_and_start_cannot_bypass_it():
    behavior = module()
    robot = Robot({'expression.audio_follow.v1', 'expression.audio_follow.v2'})
    owner = behavior.RobotBehavior(robot)
    await owner.start()
    robot.fail_disable = True
    with pytest.raises(RuntimeError, match='清理'):
        await owner.stop()
    assert owner.snapshot()['cleanup_pending']
    with pytest.raises(RuntimeError, match='清理'):
        await owner.start()
    robot.fail_disable = False
    await owner.stop()
    assert not owner.snapshot()['cleanup_pending']


@run_async
async def test_changed_identity_never_receives_previous_owner_cleanup():
    behavior = module()
    robot = Robot({'expression.audio_follow.v1', 'expression.audio_follow.v2'})
    owner = behavior.RobotBehavior(robot)
    await owner.start()
    previous = len(robot.calls)
    robot.device_info['device_id'] = 'robot-B'
    with pytest.raises(RuntimeError):
        await owner.stop()
    assert len(robot.calls) == previous
    assert owner.snapshot()['cleanup_pending']
    robot.device_info['device_id'] = 'robot-A'
    await owner.stop()
    assert not owner.snapshot()['cleanup_pending']


@run_async
async def test_cancelled_start_drains_applied_command_then_disables_display():
    behavior = module()
    robot = Robot({'expression.audio_follow.v1', 'expression.audio_follow.v2'})
    entered, released = threading.Event(), threading.Event()
    original = robot.follow
    def blocked(enabled, *, emotion=None):
        if enabled:
            entered.set()
            released.wait(2)
        original(enabled, emotion=emotion)
    robot.expression_runtime.set_audio_follow = blocked
    owner = behavior.RobotBehavior(robot)
    task = asyncio.create_task(owner.start())
    await until(entered.is_set)
    task.cancel()
    await asyncio.sleep(.02)
    assert not task.done()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert robot.calls[-1] == ('expression', False, None)
    assert not owner.snapshot()['cleanup_pending']


@run_async
async def test_failed_motion_is_stopped_without_disabling_voice_or_expression():
    behavior = module()
    robot, clock = Robot(), Clock()
    owner = behavior.RobotBehavior(robot, clock=clock)
    await owner.start()
    await owner.set_phase('speaking', text='开心')
    await until(lambda: bool(robot.jobs))
    robot.jobs[-1].state = 'failed'
    await until(lambda: ('stop_motion',) in robot.calls)
    assert owner.active and not owner.snapshot()['motion_available']
    await owner.set_phase('thinking')
    await until(lambda: ('expression', True, 4) in robot.calls)
    await owner.stop()


@run_async
async def test_lost_enable_ack_retains_disable_responsibility_but_voice_can_continue():
    behavior = module()
    robot = Robot({'expression.audio_follow.v1', 'expression.audio_follow.v2'})
    original = robot.follow
    def applied_without_ack(enabled, *, emotion=None):
        original(enabled, emotion=emotion)
        if enabled:
            raise TimeoutError('lost ack with private device details')
    robot.expression_runtime.set_audio_follow = applied_without_ack
    owner = behavior.RobotBehavior(robot)
    await owner.start()
    assert owner.snapshot()['degraded'] and owner.snapshot()['cleanup_pending']
    assert 'private' not in owner.snapshot()['error']
    await owner.set_phase('listening')
    await owner.stop()
    assert robot.calls[-1] == ('expression', False, None)
    assert not owner.snapshot()['cleanup_pending']


@run_async
async def test_old_sdk_emotion_signature_degrades_before_any_display_command():
    behavior = module()
    robot = Robot({'expression.audio_follow.v1', 'expression.audio_follow.v2'})
    robot.expression_runtime.set_audio_follow = lambda enabled: robot.calls.append(('old', enabled))
    owner = behavior.RobotBehavior(robot)
    await owner.start()
    await owner.set_phase('thinking')
    await owner.stop()
    assert owner.snapshot()['degraded']
    assert not robot.calls


@run_async
async def test_cancelled_stop_drains_teardown_and_does_not_reopen_resources():
    behavior = module()
    robot = Robot({'expression.audio_follow.v1', 'expression.audio_follow.v2'})
    entered, released = threading.Event(), threading.Event()
    original = robot.follow
    def blocked(enabled, *, emotion=None):
        if not enabled:
            entered.set()
            released.wait(2)
        original(enabled, emotion=emotion)
    robot.expression_runtime.set_audio_follow = blocked
    owner = behavior.RobotBehavior(robot)
    await owner.start()
    task = asyncio.create_task(owner.stop())
    await until(entered.is_set)
    task.cancel()
    await asyncio.sleep(.02)
    assert not task.done()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not owner.snapshot()['active']
    assert not owner.snapshot()['cleanup_pending']
    assert robot.calls[-1] == ('expression', False, None)


@run_async
async def test_minimal_robot_without_optional_domains_degrades_without_attribute_error():
    behavior = module()
    owner = behavior.RobotBehavior(SimpleNamespace(device_info={}))
    await owner.start()
    await owner.set_phase('listening')
    await owner.stop()
    assert owner.snapshot()['degraded']
    assert not owner.snapshot()['cleanup_pending']


@run_async
async def test_listening_stops_inflight_action_before_return_without_waiting_for_clip():
    behavior = module()
    robot, clock = Robot(), Clock()
    owner = behavior.RobotBehavior(robot, clock=clock)
    await owner.start()
    await owner.set_phase('speaking', text='我很开心')
    await until(lambda: ('action', 'chat_v1_happy') in robot.calls)
    # The authored 2660 ms timeline has not elapsed and Job can remain running.
    robot.jobs[-1].state = 'running'
    await asyncio.wait_for(owner.set_phase('listening'), .3)
    assert ('stop_motion',) in robot.calls
    assert not owner._motion_owned
    assert owner.active
    await owner.set_phase('speaking', text='有点伤心')
    await until(lambda: ('action', 'chat_v1_sad') in robot.calls)
    await owner.stop()


@run_async
async def test_explicit_expression_survives_slow_reply_then_holds_four_seconds_of_speaking():
    behavior = module()
    robot, clock = Robot(), Clock()
    owner = behavior.RobotBehavior(robot, clock=clock)
    await owner.start()
    await owner.set_phase('listening')
    owner.user_cue('请展示生气的表情')
    await owner.set_phase('thinking')
    clock.advance(11)
    await owner.set_phase('speaking', text='当然可以，我很开心。')
    assert owner.snapshot()['emotion'] == 3
    clock.advance(3.9)
    assert owner.snapshot()['emotion'] == 3
    clock.advance(.2)
    assert owner.snapshot()['emotion'] == 2
    await owner.set_phase('listening')
    owner.user_cue('不要展示生气的表情')
    await owner.set_phase('speaking', text='当然可以，我很开心。')
    assert owner.snapshot()['emotion'] == 2
    await owner.stop()


@run_async
async def test_failed_listening_stop_keeps_ownership_until_retry_and_never_starts_a_new_clip():
    behavior = module()
    robot, clock = Robot(), Clock()
    owner = behavior.RobotBehavior(robot, clock=clock)
    await owner.start()
    await owner.set_phase('speaking', text='我很开心')
    await until(lambda: bool(robot.jobs))
    original = robot.motion.stop
    robot.motion.stop = lambda: (_ for _ in ()).throw(RuntimeError('private details'))
    with pytest.raises(RuntimeError, match='停止'):
        await owner.set_phase('listening')
    assert owner._motion_owned and owner.snapshot()['cleanup_pending']
    assert 'private' not in owner.snapshot()['error']
    robot.motion.stop = original
    await owner.stop()
    assert not owner.snapshot()['cleanup_pending']


@run_async
async def test_local_pcm_mouth_sync_requires_new_firmware_capability_and_available_expression():
    behavior = module()
    base = {'expression.audio_follow.v1', 'expression.audio_follow.v2'}
    robot = Robot(base | {'expression.audio_follow.local_pcm.v1'})
    owner = behavior.RobotBehavior(robot)
    assert not owner.snapshot()['mouth_sync_available']
    await owner.start()
    assert owner.snapshot()['mouth_sync_available']
    await owner.stop()
    legacy = behavior.RobotBehavior(Robot(base))
    await legacy.start()
    assert not legacy.snapshot()['mouth_sync_available']
    await legacy.stop()
    missing_expression = behavior.RobotBehavior(Robot({'expression.audio_follow.local_pcm.v1'}))
    await missing_expression.start()
    assert not missing_expression.snapshot()['mouth_sync_available']
    await missing_expression.stop()


@pytest.mark.parametrize(('text', 'expected'), [
    ('好的，我不会生气。', 0),
    ('不要生气，我们慢慢来。', 0),
    ('我不再感到难过。', 0),
    ('我并没有觉得愤怒。', 0),
    ('我不会生气，不过这件事让我很难过。', 5),
    ('不要生气，今天我很开心。', 2),
    ('我不是生气，只是有点失落。', 5),
    ('不要开心得太早。', 0),
    ('我不开心。', 5),
    ('我不是很开心。', 5),
    ('我不会不开心。', 0),
    ('我没有办法接受这个结果，我很生气。', 3),
])
def test_reply_sentiment_ignores_explicitly_negated_emotions_without_erasing_later_clauses(text, expected):
    assert module().ExpressionPolicy.infer(text) == expected
