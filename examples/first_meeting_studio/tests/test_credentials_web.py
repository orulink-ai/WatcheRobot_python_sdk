import re
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from studio.config import ConfigStore
from studio.service import MeetingService
from studio.web import create_web_app


def test_credentials_form_save_clear_and_missing_preflight(tmp_path):
    store = ConfigStore(tmp_path / 'settings.json')
    service = MeetingService(SimpleNamespace(), SimpleNamespace(), store.settings, tmp_path)
    with TestClient(create_web_app(service, store, Path(__file__).resolve().parents[1] / 'web')) as client:
        html = client.get('/').text
        assert 'name="tts_token" type="password"' in html
        client.headers['X-Meeting-Token'] = re.search('name="meeting-token" content="([^"]+)', html)[1]
        result = client.post('/api/start', json={'rehearsal': True})
        assert result.status_code == 409
        assert '语音凭据' in result.json()['error']
        result = client.post('/api/config', json={'tts_token': 'fake-private-token'})
        assert result.status_code == 200
        assert 'fake-private-token' not in result.text
        assert result.json()['tts_token_configured']
        service.cleanup_required = True
        assert client.post('/api/config/clear-credentials').status_code == 409
        service.cleanup_required = False
        assert client.post('/api/config/clear-credentials').status_code == 200
        assert not store.settings.tts_token
