import asyncio
import threading
from types import SimpleNamespace

import pytest

from meeting.robot import SDKRobot


def adapter(events, *, fail_stop=False, fail_capture=False, fail_resume=False, cancel_capture=False):
    stopped = threading.Event()
    starts = 0
    def start(**kwargs):
        nonlocal starts
        starts += 1
        events.append('start')
        if starts > 1 and fail_resume:
            raise RuntimeError('resume failed')
    def stop(**kwargs):
        events.append('stop')
        if fail_stop:
            raise RuntimeError('stop failed')
    def capture(**kwargs):
        events.append('capture')
        if cancel_capture:
            stopped.set()
        if fail_capture:
            raise RuntimeError('capture failed')
        return SimpleNamespace(save=lambda path: events.append('save'))
    sdk = SimpleNamespace(
        vision=SimpleNamespace(status=lambda **kw: SimpleNamespace(model=SimpleNamespace(contains_face_class=True))),
        face_tracking=SimpleNamespace(start=start, stop=stop),
        camera=SimpleNamespace(capture=capture))
    return SDKRobot(sdk, stopped)


def test_photo_pauses_and_resumes_only_owned_tracking(tmp_path):
    events = []
    robot = adapter(events)
    async def run():
        await robot.start_tracking()
        await robot.photo(tmp_path / 'photo.jpg')
    asyncio.run(run())
    assert events == ['start', 'stop', 'capture', 'save', 'start']
    assert robot.tracking_active


def test_failed_stop_never_starts_camera(tmp_path):
    events = []
    robot = adapter(events, fail_stop=True)
    async def run():
        await robot.start_tracking()
        await robot.photo(tmp_path / 'photo.jpg')
    with pytest.raises(RuntimeError, match='stop failed'):
        asyncio.run(run())
    assert events == ['start', 'stop']


def test_failed_photo_still_restores_tracking(tmp_path):
    events = []
    robot = adapter(events, fail_capture=True)
    async def run():
        await robot.start_tracking()
        await robot.photo(tmp_path / 'photo.jpg')
    with pytest.raises(RuntimeError, match='capture failed'):
        asyncio.run(run())
    assert events == ['start', 'stop', 'capture', 'start']


def test_stop_during_capture_never_restarts_tracking(tmp_path):
    events = []
    robot = adapter(events, cancel_capture=True)
    async def run():
        await robot.start_tracking()
        await robot.photo(tmp_path / 'photo.jpg')
    with pytest.raises(InterruptedError):
        asyncio.run(run())
    assert events == ['start', 'stop', 'capture']


def test_resume_failure_keeps_photo_success_and_reports_following_error(tmp_path):
    events = []
    robot = adapter(events, fail_resume=True)
    async def run():
        await robot.start_tracking()
        await robot.photo(tmp_path / 'photo.jpg')
    asyncio.run(run())
    assert 'save' in events and not robot.tracking_active
    assert str(robot.tracking_resume_error) == 'resume failed'
