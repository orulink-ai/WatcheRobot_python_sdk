import re
from pathlib import Path

from fastapi.testclient import TestClient

from meeting.config import ConfigStore
from meeting.web import create_web_app
from test_flow import service


def make_client(tmp_path):
    store = ConfigStore(tmp_path / 'settings.json')
    store.update({'tts_token': 'private-secret'})
    app = create_web_app(service(tmp_path), store, Path(__file__).resolve().parents[1] / 'web')
    client = TestClient(app)
    token = re.search(r'name="meeting-token" content="([^"]+)"', client.get('/').text)[1]
    return client, {'x-meeting-token': token}


def test_web_requires_token_and_rejects_cross_origin(tmp_path):
    client, headers = make_client(tmp_path)
    assert client.get('/api/config').status_code == 403
    response = client.get('/api/config', headers=headers)
    assert response.status_code == 200
    assert 'private-secret' not in response.text
    assert client.post('/api/stop', headers={**headers, 'origin': 'https://evil.example'}).status_code == 403


def test_validation_does_not_echo_secret_and_offline_start_is_rejected(tmp_path):
    client, headers = make_client(tmp_path)
    response = client.post('/api/config', headers=headers, json={'tts_token': {'secret': 'hidden-value'}})
    assert response.status_code == 422
    assert 'hidden-value' not in response.text
    assert client.post('/api/start', headers=headers, json={}).status_code == 409


def test_cross_platform_entrypoints_share_arguments_and_exit_codes():
    root = Path(__file__).resolve().parents[1]
    assert 'launch.py' in (root / 'run.ps1').read_text()
    assert 'launch.py' in (root / 'run.sh').read_text()
