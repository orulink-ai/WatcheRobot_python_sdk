import asyncio
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.config import ConfigStore
from studio.robot import SDKRobot
from watcherobot.distribution.source_files import collect_application_source_files


def test_publication_excludes_personal_runtime_data(tmp_path):
    (tmp_path / '.watcherignore').write_text((ROOT / '.watcherignore').read_text())
    for name in ('artifacts/settings.json', 'artifacts/photos/private.jpg', 'artifacts/meeting.log', 'app.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('sample')
    assert [p.as_posix() for p in collect_application_source_files(tmp_path)] == ['app.py']


def test_credentials_can_be_cleared_without_echoing_secrets(tmp_path):
    store = ConfigStore(tmp_path / 'settings.json')
    store.update({'tts_token': 'test-private', 'stt_token': 'test-private'})
    assert 'test-private' not in str(store.public())
    store.update({'tts_token': ''})
    assert store.settings.tts_token == 'test-private'
    store.clear_credentials()
    assert not ConfigStore(store.path).settings.tts_token
    assert not store.settings.stt_token


@pytest.mark.parametrize('failures', [1, 2])
def test_stop_retries_lost_ack_but_never_hides_persistent_failure(failures):
    calls = []
    def stop():
        calls.append('audio')
        if len(calls) <= failures:
            raise TimeoutError('missing ACK')
    sdk = SimpleNamespace(audio=SimpleNamespace(stop=stop), motion=SimpleNamespace(stop=lambda: None),
                          expression_runtime=SimpleNamespace(stop=lambda: None))
    robot = SDKRobot(sdk, threading.Event())
    if failures == 2:
        with pytest.raises(RuntimeError, match='audio.stop:TimeoutError'):
            asyncio.run(robot.stop())
    else:
        asyncio.run(robot.stop())
    assert len(calls) == 2
