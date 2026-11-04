from studio.config import ConfigStore, Settings
from studio.cloud import VolcCloud

def test_api_key_auth_and_secret_lifecycle(tmp_path):
    store = ConfigStore(tmp_path / 'settings.json')
    store.update({'speech_api_key': 'test-private-key'})
    assert store.settings.missing_speech_fields() == []
    cloud = VolcCloud(store.settings, client=object())
    for service in ('tts', 'stt'):
        headers = cloud.headers(service, 'resource')
        assert headers['X-Api-Key'] == 'test-private-key'
        assert 'X-Api-App-Key' not in headers
    assert store.public()['speech_api_key'] == ''
    assert store.public()['speech_api_key_configured']
    assert 'test-private-key' not in store.redact('test-private-key')
    store.update({'speech_api_key': ''})
    assert store.settings.speech_api_key == 'test-private-key'
    store.clear_credentials()
    assert not store.settings.speech_api_key
    assert store.settings.missing_speech_fields()

def test_legacy_auth_remains_supported():
    cloud = VolcCloud(Settings(tts_app_id='app', tts_token='token'), client=object())
    assert cloud.headers('tts', 'resource')['X-Api-Access-Key'] == 'token'
