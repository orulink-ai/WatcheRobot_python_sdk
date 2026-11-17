from fastapi.testclient import TestClient

from watcherobot.idle_studio.server import create_app
from watcherobot.runtime.daemon.maintenance.service import MaintenanceError


def test_preview_progress_is_scoped_to_request():
    class Reader:
        def preview(self, port, resource, device_id=None, request_id=None):
            assert request_id == 'preview-1'
            return b'GIF89a'

        def preview_progress(self, request_id):
            assert request_id == 'preview-1'
            return {'phase': 'reading', 'received': 4096, 'total': 8192}

    client = TestClient(create_app(Reader()))
    assert client.get('/api/preview', params={
        'port': 'TEST', 'resource': 'blink', 'request_id': 'preview-1',
    }).status_code == 200
    response = client.get('/api/preview-progress?request_id=preview-1')
    assert response.json() == {'phase': 'reading', 'received': 4096, 'total': 8192}
    assert response.headers['cache-control'] == 'no-store'


def test_save_transport_error_preserves_actionable_message():
    class Reader:
        def save(self, *args):
            raise MaintenanceError('USB 超时，请重新读取设备')

    client = TestClient(create_app(Reader()))
    response = client.post('/api/save', json={
        'port': 'TEST', 'device_id': 'device', 'revision': 0, 'profile': {},
    })
    assert response.status_code == 409
    assert 'USB 超时' in response.json()['detail']


def test_foreign_origin_cannot_write_device():
    class Reader:
        def save(self, *args):
            raise AssertionError('must not touch device')

    client = TestClient(create_app(Reader()))
    response = client.post('/api/save', json={}, headers={'Origin': 'https://other.example'})
    assert response.status_code == 403


def test_preview_returns_image_and_reports_device_errors():
    class Reader:
        def preview(self, port, resource, device_id=None):
            assert port == 'TEST'
            if resource == 'missing':
                raise ValueError('素材不存在')
            return b'GIF89a'
    client = TestClient(create_app(Reader()))
    response = client.get('/api/preview', params={'port': 'TEST', 'resource': 'blink'})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/gif'
    assert response.content == b'GIF89a'
    assert client.get('/api/preview', params={'port': 'TEST', 'resource': 'missing'}).status_code == 409
