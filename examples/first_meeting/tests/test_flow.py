import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meeting.dialogue import Dialogue
from meeting.config import Settings, ConfigStore
from meeting.service import MeetingService


class Robot:
    def __init__(self):
        self.events = []
        self.fail_photo = False

    async def expression(self, name):
        self.events.append(('expression', name))

    async def prefetch(self, name):
        pass

    async def finish_expression(self):
        pass

    async def move(self, pan, tilt, duration):
        self.events.append(('move', pan, tilt))

    async def speak(self, pcm):
        self.events.append(('speak', pcm))

    async def photo(self, path):
        self.events.append(('photo', str(path)))
        if self.fail_photo:
            raise RuntimeError('camera failed')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'jpeg')

    async def stop(self):
        self.events.append(('stop',))


class Cloud:
    async def tts(self, text):
        return text

    async def chat(self, messages):
        return '你平时喜欢做什么呀？'


def service(tmp_path):
    settings = Settings(sleep_seconds=0, look_seconds=0, voice_enabled=False)
    return MeetingService(Robot(), Cloud(), settings, tmp_path)


@pytest.mark.parametrize('answer', ['可以', '好呀', '可以，拍吧', '我同意', '你可以给我拍照'])
def test_explicit_consent_only_after_question(answer):
    d = Dialogue()
    assert d.next(answer).photo is False
    assert d.next('我叫小明').photo is False
    assert d.next(answer).photo is True


@pytest.mark.parametrize('answer', ['不可以', '可以聊天但不要拍照', '不太可以', '我没说可以', '你说可以是什么意思', '如果可以的话', '可以吗'])
def test_negation_or_ambiguity_never_authorizes_camera(answer):
    d = Dialogue()
    d.next('我叫小明')
    assert d.next(answer).photo is False


def test_boot_order_and_motion(tmp_path):
    s = service(tmp_path)
    asyncio.run(s.boot())
    events = s.robot.events
    expressions = [e[1] for e in events if e[0] == 'expression']
    assert expressions[:5] == ['standby_loop', 'standby_end', 'standby3', 'standby4', 'blink']
    moves = [e[1:] for e in events if e[0] == 'move']
    assert moves == [(90, 100), (90, 120), (95, 120), (108, 120), (90, 120), (78, 120), (90, 120)]
    assert '这是什么地方呀' in events[-1][1]


def test_photo_success_is_persisted_before_acknowledgement(tmp_path):
    async def run():
        s = service(tmp_path)
        await s.turn('我叫小明')
        await s.turn('可以')
        assert (tmp_path / 'person.json').exists()
        events = s.robot.events
        assert next(i for i, e in enumerate(events) if e[0] == 'photo') < next(i for i, e in enumerate(events) if e[0] == 'speak' and '记住你' in e[1])
        await s.turn('可以')
        assert len([e for e in events if e[0] == 'photo']) == 1
    asyncio.run(run())


def test_failed_capture_never_claims_memory(tmp_path):
    async def run():
        s = service(tmp_path)
        s.robot.fail_photo = True
        await s.turn('我叫小明')
        await s.turn('可以')
        assert not (tmp_path / 'person.json').exists()
        assert not any(e[0] == 'speak' and '记住你' in e[1] for e in s.robot.events)
        assert s.dialogue.stage == 'consent'
    asyncio.run(run())


def test_device_errors_keep_action_and_reason_but_hide_secrets(tmp_path):
    from watcherobot.errors import CommandError
    s = service(tmp_path)
    s.settings.tts_token = 'private-device-token'
    detail = s.error_detail(CommandError('ctrl.audio.start', 'busy private-device-token'))
    assert 'ctrl.audio.start' in detail and 'busy' in detail
    assert 'private-device-token' not in detail
    assert 'JPEG' in s.error_detail(TimeoutError('camera did not return a JPEG before timeout'))
    assert s.error_detail(TimeoutError()) == 'TimeoutError'
    assert s.error_detail(ValueError('unexpected secret')) == 'ValueError'


def test_secret_masking_and_blank_preserves_existing(tmp_path):
    store = ConfigStore(tmp_path / 'settings.json')
    store.update({'tts_token': 'private-token'})
    assert 'private-token' not in str(store.public())
    store.update({'tts_token': ''})
    assert store.settings.tts_token == 'private-token'
    with pytest.raises(ValueError):
        store.update({'tilt_up': 160})
    assert store.settings.tilt_up == 120


def test_stop_during_tts_cancels_cloud_and_never_starts_speaker(tmp_path):
    async def run():
        s = service(tmp_path)
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        async def slow_tts(text):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        s.cloud.tts = slow_tts
        s.start(boot=False)
        await asyncio.sleep(0)
        s.submit('我叫测试员')
        await entered.wait()
        s.request_stop()
        await asyncio.wait_for(s.task, timeout=1)
        assert cancelled.is_set()
        assert not any(e[0] == 'speak' for e in s.robot.events)
        assert s.phase == 'stopped'
    asyncio.run(run())


def test_duplicate_start_does_not_overlap_or_reset_consent(tmp_path):
    async def run():
        s = service(tmp_path)
        s.start(boot=False)
        with pytest.raises(ValueError):
            s.start()
        s.request_stop()
        await s.task
    asyncio.run(run())
