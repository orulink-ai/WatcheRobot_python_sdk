"""Idle unselection must not restart the shared Daemon or disconnect peers."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from watcherobot.runtime.daemon.application.session import ApplicationState
from watcherobot.runtime.daemon.control.rest import DaemonControlAPI
from watcherobot.runtime.daemon.runtime import DaemonRuntime
from tests.runtime.test_daemon_runtime_routing import (
    _connect_as, _select_python_application, _write_relay_application,
)


def _runtime(root: Path) -> DaemonRuntime:
    application = root / "application"
    _write_relay_application(application)
    runtime = DaemonRuntime(
        application_dir=application, current_app="test_app",
        managed_app_root=Path(sys.executable).parent,
        external_host="127.0.0.1", external_port=0, control_port=0,
        pairing_udp_port=0, preview_udp_port=0,
    )
    _select_python_application(runtime, application)
    return runtime


def test_idle_unselect_clears_launch_spec_and_is_idempotent(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.application.last_exit_code = 42
    client = TestClient(DaemonControlAPI(controller=runtime).create_app())
    for _ in range(2):
        response = client.post('/daemon/application/unselect', json={'application_id': 'test_app'})
        assert response.status_code == 200
        assert response.json()['application'] == {
            'selected': False, 'current_app': None, 'state': 'not_selected',
            'process_id': None, 'last_exit_code': None,
        }
    assert runtime.application.launch_spec is None
    assert runtime.application._application_dir is None
    assert client.post('/daemon/application/start').json()['error'] == 'application_not_selected'


@pytest.mark.parametrize('state', [ApplicationState.STARTING, ApplicationState.RUNNING, ApplicationState.ERROR])
def test_unselect_rejects_active_sessions_without_mutation(tmp_path: Path, state: ApplicationState) -> None:
    runtime = _runtime(tmp_path)
    run = runtime.application.registry.begin_start()
    run.state = state
    original = runtime.application.launch_spec
    client = TestClient(DaemonControlAPI(controller=runtime).create_app())
    response = client.post('/daemon/application/unselect', json={'application_id': 'test_app'})
    assert response.status_code == 409
    assert response.json()['error'] == 'application_occupied'
    assert runtime.application.registry.active_run is run
    assert runtime.application.launch_spec is original
    assert runtime.application.registry.current_app == 'test_app'


def test_unselect_rejects_pending_lifecycle_and_selection_mismatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = _runtime(tmp_path)
        api = DaemonControlAPI(controller=runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.create_app()), base_url='http://test') as client:
            async with runtime.application._operation_lock:
                response = await client.post('/daemon/application/unselect', json={'application_id': 'test_app'})
                assert response.status_code == 409
                assert response.json()['error'] == 'application_occupied'
            response = await client.post('/daemon/application/unselect', json={'application_id': 'other_app'})
            assert response.status_code == 409
            assert response.json()['error'] == 'application_selection_changed'
            api._starting_requests = 1
            response = await client.post('/daemon/application/unselect', json={'application_id': 'test_app'})
            assert response.status_code == 409
            assert runtime.application.registry.current_app == 'test_app'
    asyncio.run(scenario())


def test_unselect_preserves_live_connections_and_transparent_routing(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = _runtime(tmp_path)
        await runtime.start()
        device = await _connect_as(runtime, 'hardware')
        desktop = await _connect_as(runtime, 'desktop')
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                url = runtime.control_server.base_url + '/daemon/application/unselect'
                await runtime.start_application()
                rejected = await client.post(url, json={'application_id': 'test_app'})
                assert rejected.status_code == 409
                await runtime.stop_application()
                metadata = runtime.runtime_metadata()
                device_status = runtime.device_status()
                response = await client.post(url, json={'application_id': 'test_app'})
                assert response.status_code == 200
                assert runtime.runtime_metadata() == metadata
                assert runtime.device_status() == device_status
                for frame in ['{"type":"ctrl.microphone.open"}', '{"type":"unknown.business"}', b'opaque']:
                    await desktop.send(frame)
                    assert await asyncio.wait_for(device.recv(), 1) == frame
                await device.send(b'device payload')
                assert await asyncio.wait_for(desktop.recv(), 1) == b'device payload'
        finally:
            await desktop.close()
            await device.close()
            await runtime.stop()
    asyncio.run(scenario())


def test_unselect_rejects_remaining_process_and_runtime_draining(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    api = DaemonControlAPI(controller=runtime)
    client = TestClient(api.create_app())
    original = runtime.application.launch_spec
    # A process may still be reaped after its session has already ended.
    runtime.application._process = object()  # type: ignore[assignment]
    response = client.post('/daemon/application/unselect', json={'application_id': 'test_app'})
    assert response.status_code == 409
    assert response.json()['error'] == 'application_occupied'
    runtime.application._process = None
    api._draining = True
    response = client.post('/daemon/application/unselect', json={'application_id': 'test_app'})
    assert response.status_code == 409
    assert response.json()['error'] == 'runtime_draining'
    assert runtime.application.launch_spec is original
    assert runtime.application.registry.current_app == 'test_app'


def test_unselect_requires_an_explicit_target(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    client = TestClient(DaemonControlAPI(controller=runtime).create_app())
    for payload in [None, {}, {'application_id': ''}, {'application_id': 'test_app', 'extra': True}]:
        assert client.post('/daemon/application/unselect', json=payload).status_code == 422
    assert runtime.application.registry.current_app == 'test_app'
