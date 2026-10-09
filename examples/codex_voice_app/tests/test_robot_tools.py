import asyncio
import io
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from robot_tools import RobotTools


@asynccontextmanager
async def exclusive():
    yield


class Robot:
    def __init__(self):
        self.moves, self.colors, self.stops = [], [], 0
        self.camera = SimpleNamespace(capture=self.capture)
        self.motion = SimpleNamespace(move_to=self.move, stop=self.stop)
        self.lights = SimpleNamespace(set_color=lambda c, **kw: self.colors.append((c, kw)))
    def capture(self, **kwargs):
        data = io.BytesIO()
        Image.new('RGB', (8, 8)).save(data, format='JPEG')
        return SimpleNamespace(data=data.getvalue(), sequence=1)
    def move(self, **kwargs):
        self.moves.append(kwargs)
        return SimpleNamespace(wait=lambda timeout: None)
    def stop(self):
        self.stops += 1


def make():
    robot = Robot()
    tools = RobotTools(robot, lambda: None, exclusive, settle=0)
    return robot, tools


def test_permissions_default_denied_and_tools_are_strict():
    async def run():
        robot, tools = make()
        denied = await tools.execute('observe_scene', {'scope': 'current'})
        assert not denied['success'] and '权限' in denied['contentItems'][0]['text']
        tools.set_permissions({'camera': True, 'upload': True})
        assert not (await tools.execute('observe_scene', {'scope': 'current', 'pan_deg': 180}))['success']
        assert not (await tools.execute('exec', {}))['success']
        assert robot.moves == []
        with pytest.raises(ValueError):
            tools.set_permissions({'camera': 1})
    asyncio.run(run())


def test_real_photo_has_same_hash_for_model_and_ui_and_bounded_scan():
    async def run():
        robot, tools = make()
        def compatible_capture(**kwargs):
            assert (kwargs['width'], kwargs['height']) == (640, 480), 'PTL requires VGA'
            return robot.capture()
        robot.camera.capture = compatible_capture
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        result = await tools.execute('observe_scene', {'scope': 'nearby'})
        assert result['success']
        assert len([c for c in result['contentItems'] if c['type'] == 'inputImage']) == 3
        assert [m['pan_deg'] for m in robot.moves] == [90, 30, 150, 90]
        assert all(m['tilt_deg'] == 120 for m in robot.moves)
        assert [m['duration_ms'] for m in robot.moves] == [2000, 4000, 4000, 2000]
        assert tools.tasks[-1]['status'] == 'attaching'
        assert len(tools.evidence) == 3
        assert all(e['sha256'] and e['capturedAt'] for e in tools.evidence)
        assert [e['panDeg'] for e in tools.evidence] == [90, 30, 150]
        assert tools.photo(tools.evidence[0]['id']).startswith(b'\xff\xd8')
        assert any('回中心' in c.get('text', '') and '已确认' in c['text'] for c in result['contentItems'])
        assert tools.validate_result(result)['success']
        tools.clear_photos()
        assert tools.evidence == []
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['send', 'completion'])
def test_failed_return_to_center_does_not_claim_confirmed_return(failure):
    async def run():
        robot, tools = make()
        original = robot.move
        def move(**kwargs):
            if len(robot.moves) == 3:
                if failure == 'send':
                    raise TimeoutError('center send receipt lost')
                def wait(timeout):
                    raise TimeoutError('center completion receipt lost')
                return SimpleNamespace(wait=wait)
            return original(**kwargs)
        robot.motion.move_to = move
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        result = await tools.execute('observe_scene', {'scope': 'nearby'})
        assert not result['success']
        assert not any('回中心已确认' in c.get('text', '') for c in result['contentItems'])
        assert not any(c['type'] == 'inputImage' for c in result['contentItems'])
    asyncio.run(run())


def test_partial_failure_is_not_success_and_never_reuses_old_photos():
    async def run():
        robot, tools = make()
        tools.set_permissions({'camera': True, 'upload': True})
        await tools.execute('observe_scene', {'scope': 'current'})
        robot.camera.capture = lambda **kw: (_ for _ in ()).throw(TimeoutError('camera timeout'))
        failed = await tools.execute('observe_scene', {'scope': 'current'})
        assert not failed['success']
        assert tools.tasks[-1]['status'] == 'failed'
        assert tools.tasks[-1]['photos'] == []
        assert not any(c['type'] == 'inputImage' for c in failed['contentItems'])
    asyncio.run(run())


def test_cancel_stops_immediately_then_drains_sdk_without_late_action():
    async def run():
        robot, tools = make()
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        entered, release = threading.Event(), threading.Event()
        def capture(**kw):
            entered.set()
            release.wait(1)
            return robot.capture()
        robot.camera.capture = capture
        operation = asyncio.create_task(tools.execute('observe_scene', {'scope': 'nearby'}))
        await asyncio.to_thread(entered.wait, 1)
        stopped = asyncio.create_task(tools.cancel())
        await asyncio.sleep(.03)
        assert robot.stops >= 1
        release.set()
        await stopped
        result = await operation
        assert not result['success']
        assert tools.tasks[-1]['status'] == 'cancelled'
        assert tools.evidence == []
        assert len(robot.moves) == 1
    asyncio.run(run())


def test_lights_are_low_brightness_no_strobe_and_cancel_restores_off():
    async def run():
        robot, tools = make()
        tools.set_permissions({'lights': True})
        assert (await tools.execute('set_lights', {'color': '#00AA88', 'brightness': .2}))['success']
        assert not (await tools.execute('set_lights', {'color': '#00AA88', 'brightness': .9}))['success']
        assert not (await tools.execute('set_lights', {'color': '#00AA88', 'brightness': True}))['success']
        await tools.cancel()
        assert robot.colors[-1][1]['brightness'] == 0
    asyncio.run(run())


def test_failed_light_ack_still_turns_light_off_and_failed_stop_latches():
    async def run():
        robot, tools = make()
        tools.set_permissions({'lights': True})
        def color(c, **kw):
            robot.colors.append((c, kw))
            if c != '#000000':
                raise TimeoutError('ACK lost after device applied light')
        robot.lights.set_color = color
        assert not (await tools.execute('set_lights', {'color': '#00FF00', 'brightness': .2}))['success']
        assert robot.colors[-1][0] == '#000000'
        robot.motion.stop = lambda: (_ for _ in ()).throw(TimeoutError('offline'))
        with pytest.raises(RuntimeError):
            await tools.cancel()
        assert not (await tools.execute('set_lights', {'color': '#00FF00', 'brightness': .2}))['success']
    asyncio.run(run())


def test_turn_photo_budget_and_result_privacy_lifecycle():
    async def run():
        robot, tools = make()
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        result = await tools.execute('observe_scene', {'scope': 'nearby'}, turn_id='one')
        assert result['success']
        assert not (await tools.execute('observe_scene', {'scope': 'current'}, turn_id='one'))['success']
        tools.clear_photos()
        sanitized = tools.validate_result(result)
        assert not sanitized['success']
        assert not any(item['type'] == 'inputImage' for item in sanitized['contentItems'])
    asyncio.run(run())


def test_failed_observation_cannot_retry_with_new_call_id_in_same_turn():
    async def run():
        robot, tools = make()
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        calls = []
        def fail(**kwargs):
            calls.append(kwargs)
            raise TimeoutError('camera')
        robot.camera.capture = fail
        await tools.execute('observe_scene', {'scope': 'current'}, turn_id='one', call_id='first')
        await tools.execute('aim_camera', {'position': 'pan_min'}, turn_id='one', call_id='retry')
        assert len(calls) == 1 and not robot.moves
        await tools.execute('observe_scene', {'scope': 'current'}, turn_id='two')
        assert len(calls) == 2
    asyncio.run(run())


def test_late_light_send_is_followed_by_final_off_after_cancel():
    async def run():
        robot, tools = make()
        tools.set_permissions({'lights': True})
        entered, release = threading.Event(), threading.Event()
        def color(c, **kwargs):
            if c != '#000000':
                entered.set()
                release.wait(1)
            robot.colors.append((c, kwargs))
        robot.lights.set_color = color
        operation = asyncio.create_task(tools.execute('set_lights', {'color': '#00FF00', 'brightness': .2}))
        await asyncio.to_thread(entered.wait, 1)
        cancelling = asyncio.create_task(tools.cancel())
        await asyncio.sleep(.03)
        release.set()
        await cancelling
        await operation
        assert robot.colors[-1][0] == '#000000' and not tools.lit
    asyncio.run(run())


def test_corrupted_jpeg_fails_with_no_ui_or_model_photo_and_stops_motion():
    async def run():
        robot, tools = make()
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        robot.camera.capture = lambda **kwargs: SimpleNamespace(data=b'\xff\xd8not-a-jpeg')
        result = await tools.execute('observe_scene', {'scope': 'nearby'}, turn_id='one')
        assert not result['success']
        assert not any(c['type'] == 'inputImage' for c in result['contentItems'])
        assert tools.tasks[-1]['status'] == 'failed' and not tools.tasks[-1]['photos']
        assert not tools.evidence and tools.camera_status == 'unavailable'
        assert robot.stops == 1 and len(robot.moves) == 1
    asyncio.run(run())


def test_requested_motion_abort_receipt_is_cancelled_not_failed():
    async def run():
        from watcherobot.errors import JobCancelledError
        from types import SimpleNamespace
        robot, tools = make()
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        entered, aborted = threading.Event(), threading.Event()
        def wait(timeout):
            entered.set()
            aborted.wait(1)
            raise JobCancelledError(1, state='was cancelled')
        robot.motion.move_to = lambda **kwargs: SimpleNamespace(wait=wait)
        robot.motion.stop = aborted.set
        operation = asyncio.create_task(tools.execute('observe_scene', {'scope':'nearby'}))
        await asyncio.to_thread(entered.wait, 1)
        await tools.cancel()
        await operation
        assert tools.tasks[-1]['status'] == 'cancelled'
        assert not tools.faulted and not tools.evidence
    asyncio.run(run())


def test_agent_can_turn_its_body_without_camera_or_upload_and_hold_the_pose():
    async def run():
        robot, tools = make()
        tools.set_permissions({'motion': True})
        result = await tools.execute('aim_camera', {'pan_deg': 45, 'pace': 'natural'})
        assert result['success']
        assert [m['pan_deg'] for m in robot.moves] == [45]
        assert robot.moves[0]['duration_ms'] == 3500
        assert not tools.evidence
        assert tools.tasks[-1]['status'] == 'completed'
        assert tools.tasks[-1]['targetPanDeg'] == 45
        assert not any(c['type'] == 'inputImage' for c in result['contentItems'])
        assert '未拍照' in result['contentItems'][0]['text']
        assert '回正' not in result['contentItems'][0]['text']
    asyncio.run(run())


def test_agent_can_select_gentle_scan_without_changing_physical_limits():
    async def run():
        robot, tools = make()
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        result = await tools.execute('observe_scene', {'scope': 'nearby', 'pace': 'gentle'})
        assert result['success']
        assert [m['pan_deg'] for m in robot.moves] == [90, 30, 150, 90]
        assert [m['duration_ms'] for m in robot.moves] == [6000, 12000, 12000, 6000]
    asyncio.run(run())


@pytest.mark.parametrize('arguments', [
    {'pan_deg': 0}, {'pan_deg': 180}, {'pan_deg': True}, {'pan_deg': 40.5},
    {'position': 'pan_min', 'pan_deg': 30}, {'position': 'pan80'},
    {'pan_deg': 30, 'pace': 'unlimited'}, {'pan_deg': 40, 'speed': 999},
])
def test_model_cannot_bypass_body_limits_or_invent_motion_parameters(arguments):
    async def run():
        robot, tools = make()
        tools.set_permissions({'motion': True})
        assert not (await tools.execute('aim_camera', arguments))['success']
        assert not robot.moves and not tools.evidence
    asyncio.run(run())
