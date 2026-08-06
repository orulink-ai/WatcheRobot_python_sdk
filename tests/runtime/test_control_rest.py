from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from watcherobot.runtime.daemon.application.launcher import ApplicationLaunchError
from watcherobot.runtime.daemon.application.runtime import ApplicationStartError
from watcherobot.runtime.daemon.application.session import (
    ApplicationRun,
    ApplicationState,
    SessionOccupiedError,
)
from watcherobot.runtime.daemon.control.rest import DaemonControlAPI, DaemonControlServer
from watcherobot.runtime.daemon.pairing.session import PairingSessionError


class _ControllerStub:
    def __init__(self) -> None:
        self.current_app = "watcher_default"
        self.state = ApplicationState.NOT_RUNNING
        self.process_id = None
        self.last_exit_code = None
        self.start_error: Exception | None = None
        self.device = {
            "state": "idle",
            "online": False,
            "mode": None,
            "request_id": None,
            "last_error": None,
        }
        self.pair_requests: list[tuple[str, str]] = []
        self.cancel_requests = 0
        self.disconnect_requests = 0
        self.selected_application_dir: str | None = None
        self.selected_launcher: tuple[str, str] | None = None
        self.select_error: Exception | None = None
        self.shutdown_requested = False
        self.logs = [
            {
                "id": 1,
                "message": "runtime starting",
                "timestamp_ms": 1_000,
            },
            {
                "id": 2,
                "message": "runtime ready",
                "timestamp_ms": 2_000,
            },
        ]

    def application_status(self) -> dict:
        return {
            "current_app": self.current_app,
            "state": self.state.value,
            "process_id": self.process_id,
            "last_exit_code": self.last_exit_code,
        }

    async def start_application(self) -> ApplicationRun:
        if self.start_error is not None:
            raise self.start_error
        self.state = ApplicationState.RUNNING
        self.process_id = 1234
        return ApplicationRun(
            app_id=self.current_app,
            credential="test-only",
            state=ApplicationState.RUNNING,
        )

    async def stop_application(self) -> None:
        self.state = ApplicationState.ENDED
        self.process_id = None

    def device_status(self) -> dict:
        return {"device": dict(self.device)}

    async def pair_device(self, pairing_code: str, target_mode: str) -> dict:
        if self.device["state"] != "idle":
            raise PairingSessionError("device_slot_occupied")
        if len(pairing_code) != 6 or not pairing_code.isdigit():
            raise PairingSessionError("invalid_pairing_code")
        self.pair_requests.append((pairing_code, target_mode))
        self.device.update(
            state="discovering",
            mode=target_mode,
            request_id="21a9dbf05ea3443480e62076f79a3b12",
            last_error=None,
        )
        return self.device_status()

    async def cancel_device_pairing(self) -> dict:
        self.cancel_requests += 1
        self.device.update(
            state="idle",
            online=False,
            mode=None,
            request_id=None,
            last_error="pairing_cancelled",
        )
        return self.device_status()

    async def disconnect_device(self) -> bool:
        self.disconnect_requests += 1
        if self.device["state"] == "idle":
            return False
        self.device.update(
            state="idle",
            online=False,
            mode=None,
            request_id=None,
            last_error=None,
        )
        return True

    def select_application(
        self,
        application_dir: str,
        launcher_kind: str,
        launcher_executable: str,
    ) -> dict:
        if self.select_error is not None:
            raise self.select_error
        self.selected_application_dir = application_dir
        self.selected_launcher = (launcher_kind, launcher_executable)
        self.current_app = "selected_app"
        self.state = ApplicationState.NOT_RUNNING
        return self.application_status()

    def request_shutdown(self) -> None:
        self.shutdown_requested = True

    def daemon_logs(self, after_id: int = 0) -> list[dict]:
        return [
            event
            for event in self.logs
            if event["id"] > after_id
        ]


def test_control_rest_manages_application_lifecycle_and_device_pairing() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    assert client.get("/daemon/status").json() == {
        "application": {
            "current_app": "watcher_default",
            "state": "not_running",
            "process_id": None,
            "last_exit_code": None,
        }
    }
    assert client.get("/daemon/logs?after_id=1").json() == {
        "logs": [
            {
                "id": 2,
                "message": "runtime ready",
                "timestamp_ms": 2_000,
            }
        ]
    }
    started = client.post("/daemon/application/start")
    assert started.status_code == 200
    assert started.json()["application"]["state"] == "running"

    stopped = client.post("/daemon/application/stop")
    assert stopped.status_code == 200
    assert stopped.json()["application"]["state"] == "ended"

    assert client.get("/daemon/devices").json() == {
        "device": {
            "state": "idle",
            "online": False,
            "mode": None,
            "request_id": None,
            "last_error": None,
        },
    }
    paired = client.post(
        "/daemon/devices/pair",
        json={"pairing_code": "123456", "target_mode": "desktop_link"},
    )
    assert paired.status_code == 202
    assert paired.json()["device"]["state"] == "discovering"
    assert controller.pair_requests == [("123456", "desktop_link")]

    occupied = client.post(
        "/daemon/devices/pair",
        json={"pairing_code": "999999", "target_mode": "desktop_link"},
    )
    assert occupied.status_code == 409
    assert occupied.json()["error"] == "device_slot_occupied"

    cancelled = client.post("/daemon/devices/pair/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["device"]["last_error"] == "pairing_cancelled"
    assert controller.cancel_requests == 1

    missing = client.post("/daemon/devices/disconnect")
    assert missing.status_code == 404
    assert missing.json()["error"] == "device_not_connected"

    controller.device.update(
        state="connected",
        online=True,
        mode="desktop_link",
        request_id="21a9dbf05ea3443480e62076f79a3b12",
    )
    disconnected = client.post("/daemon/devices/disconnect")
    assert disconnected.status_code == 200
    assert disconnected.json() == {
        "disconnected": True,
        "device": {
            "state": "idle",
            "online": False,
            "mode": None,
            "request_id": None,
            "last_error": None,
        },
    }
    assert controller.disconnect_requests == 2

    assert client.post("/daemon/business/frame").status_code == 404
    assert client.post("/daemon/devices/17/disconnect").status_code == 404
    assert (
        client.post(
            "/daemon/devices/legacy-request/approval",
            json={"action": "approve"},
        ).status_code
        == 404
    )


def test_control_rest_reports_occupied_and_start_failure() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    controller.start_error = SessionOccupiedError("already running")
    occupied = client.post("/daemon/application/start")
    assert occupied.status_code == 409
    assert occupied.json()["error"] == "application_occupied"

    controller.start_error = ApplicationStartError("entrypoint failed")
    failed = client.post("/daemon/application/start")
    assert failed.status_code == 500
    assert failed.json()["error"] == "application_start_failed"


def test_control_rest_allows_only_local_desktop_origins() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    for origin in (
        "http://tauri.localhost",
        "tauri://localhost",
        "http://localhost:54321",
        "http://127.0.0.1:54321",
        "null",
    ):
        response = client.get(
            "/daemon/status",
            headers={"Origin": origin},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin

    rejected = client.get(
        "/daemon/status",
        headers={"Origin": "https://example.com"},
    )
    assert rejected.status_code == 200
    assert "access-control-allow-origin" not in rejected.headers


def test_control_rest_allows_private_network_preflight_for_local_desktop() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    response = client.options(
        "/daemon/status",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"
    assert response.headers["access-control-allow-private-network"] == "true"


def test_control_server_binds_and_serves_status() -> None:
    async def scenario() -> None:
        controller = _ControllerStub()
        server = DaemonControlServer(
            controller=controller,
            host="127.0.0.1",
            port=0,
        )
        await server.start()
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{server.base_url}/daemon/status")
            assert response.status_code == 200
            assert response.json()["application"]["state"] == "not_running"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_control_rest_selects_application_and_requests_runtime_shutdown() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    selected = client.post(
        "/daemon/application/select",
        json={
            "application_dir": "C:/apps/demo/source",
            "launcher": {
                "kind": "python",
                "executable": "C:/apps/demo/.venv/Scripts/python.exe",
            },
        },
    )
    stopped = client.post("/daemon/stop")

    assert selected.status_code == 200
    assert selected.json()["application"]["current_app"] == "selected_app"
    assert controller.selected_application_dir == "C:/apps/demo/source"
    assert controller.selected_launcher == (
        "python",
        "C:/apps/demo/.venv/Scripts/python.exe",
    )
    assert stopped.status_code == 202
    assert controller.shutdown_requested is True


def test_control_rest_rejects_legacy_or_arbitrary_launcher_requests() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    legacy = client.post(
        "/daemon/application/select",
        json={"application_dir": "C:/apps/demo/source"},
    )
    arbitrary_arguments = client.post(
        "/daemon/application/select",
        json={
            "application_dir": "C:/apps/demo/source",
            "launcher": {
                "kind": "python",
                "executable": "C:/apps/demo/.venv/Scripts/python.exe",
                "args": ["-c", "arbitrary code"],
            },
        },
    )

    assert legacy.status_code == 422
    assert arbitrary_arguments.status_code == 422
    assert controller.selected_application_dir is None


def test_control_rest_maps_invalid_launcher_to_stable_error() -> None:
    controller = _ControllerStub()
    controller.select_error = ApplicationLaunchError(
        "launcher escaped its controlled root"
    )
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    response = client.post(
        "/daemon/application/select",
        json={
            "application_dir": "C:/apps/demo/source",
            "launcher": {
                "kind": "python",
                "executable": "C:/outside/python.exe",
            },
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_application_launcher",
        "message": "launcher escaped its controlled root",
    }


def test_control_rest_does_not_own_application_catalog_mutation() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    assert client.get("/daemon/applications").status_code == 404
    assert (
        client.post(
            "/daemon/applications/install",
            json={"application_id": "demo"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/daemon/applications/select",
            json={"app_id": "demo", "version": "1.0.0"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/daemon/applications/uninstall",
            json={"app_id": "demo", "version": "1.0.0"},
        ).status_code
        == 404
    )
