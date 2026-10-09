"""A stale caller must never start another caller's Application selection."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from watcherobot.runtime.daemon.application.session import ApplicationSelectionChangedError
from watcherobot.runtime.daemon.control.rest import DaemonControlAPI
from tests.runtime.test_application_unselect import _runtime
from tests.runtime.test_daemon_runtime_routing import _select_python_application, _write_relay_application


@pytest.mark.parametrize('other_id', ['test_app', 'different_app'])
def test_changed_selection_rejects_start_without_spawning(tmp_path, other_id):
    runtime = _runtime(tmp_path)
    token = runtime.application_status()['selection_id']
    other = tmp_path / 'other'
    _write_relay_application(other)
    manifest = json.loads((other / 'app.json').read_text())
    manifest['id'] = other_id
    (other / 'app.json').write_text(json.dumps(manifest))
    _select_python_application(runtime, other)
    # Even reselecting the same app ID must invalidate the previous choice.
    assert runtime.application_status()['selection_id'] != token
    client = TestClient(DaemonControlAPI(controller=runtime).create_app())
    response = client.post('/daemon/application/start-selected', json={'selection_id': token})
    assert response.status_code == 409
    assert response.json()['error'] == 'application_selection_changed'
    assert runtime.application.process_id is None
    assert runtime.application.launch_spec.application_dir == other


def test_selection_checked_after_waiting_for_lifecycle_lock(tmp_path):
    async def scenario():
        runtime = _runtime(tmp_path)
        manager = runtime.application
        token = manager.selection_id
        await manager._operation_lock.acquire()
        start = asyncio.create_task(manager.start(expected_selection_id=token))
        await asyncio.sleep(0)
        _select_python_application(runtime, tmp_path / 'application')
        manager._operation_lock.release()
        with pytest.raises(ApplicationSelectionChangedError):
            await start
        assert manager.process_id is None
    asyncio.run(scenario())


def test_bound_start_succeeds_and_legacy_start_remains_supported(tmp_path):
    async def scenario():
        runtime = _runtime(tmp_path)
        api = DaemonControlAPI(controller=runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.create_app()), base_url='http://test') as client:
            try:
                token = runtime.application_status()['selection_id']
                response = await client.post('/daemon/application/start-selected', json={'selection_id': token})
                assert response.status_code == 200
                assert response.json()['application']['selection_id'] == token
                await runtime.stop_application()
                assert (await client.post('/daemon/application/start')).status_code == 200
            finally:
                await runtime.stop_application()
    asyncio.run(scenario())


def test_bound_start_requires_explicit_nonempty_token(tmp_path):
    client = TestClient(DaemonControlAPI(controller=_runtime(tmp_path)).create_app())
    for payload in ({}, {'selection_id': ''}):
        assert client.post('/daemon/application/start-selected', json=payload).status_code == 422
