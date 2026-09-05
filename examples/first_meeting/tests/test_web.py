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


def test_device_polling_does_not_block_motion_loop_and_reuses_client(tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from meeting import web

    clients = []
    loop_progress = threading.Event()

    class Client:
        def __init__(self, **kwargs):
            self.loop_responsive = loop_progress.wait(0.3)
            self.closed = 0
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.aclose()

        async def get(self, url):
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {'device': {'online': True}})

        async def aclose(self):
            self.closed += 1

    monkeypatch.setattr(web.httpx, 'AsyncClient', Client)

    async def run():
        app = create_web_app(service(tmp_path), ConfigStore(tmp_path / 'settings.json'),
                             tmp_path, 'http://127.0.0.1/device')
        asyncio.get_running_loop().call_soon(loop_progress.set)
        assert (await app.state.refresh_device())['online']
        assert clients[0].loop_responsive, 'Status client setup blocked the motion event loop'
        assert (await app.state.refresh_device())['online']
        assert len(clients) == 1
        await app.state.close_device_client()
        assert clients[0].closed == 1

    asyncio.run(run())


def test_cancelled_status_poll_still_closes_initializing_client(tmp_path, monkeypatch):
    import asyncio
    import threading
    from meeting import web

    started, release = threading.Event(), threading.Event()
    closed = []

    class Client:
        def __init__(self, **kwargs):
            started.set()
            release.wait(1)

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(web.httpx, 'AsyncClient', Client)

    async def run():
        app = create_web_app(service(tmp_path), ConfigStore(tmp_path / 'settings.json'),
                             tmp_path, 'http://127.0.0.1/device')
        poll = asyncio.create_task(app.state.refresh_device())
        try:
            assert await asyncio.to_thread(started.wait, 1)
            poll.cancel()
            await asyncio.gather(poll, return_exceptions=True)
        finally:
            release.set()
        await app.state.close_device_client()
        assert closed == [True]

    asyncio.run(run())
