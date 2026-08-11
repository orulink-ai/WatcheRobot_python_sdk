from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

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

    def select_application(self, application_dir: str) -> dict:
        self.selected_application_dir = application_dir
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


def test_control_rest_serves_local_test_console() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    redirect = client.get("/control", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/control/"

    page = client.get("/control/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert 'id="pairing-form"' in page.text
    assert 'inputmode="numeric"' in page.text
    assert 'data-service="daemon"' in page.text
    assert 'data-service="device"' in page.text
    assert 'data-service="application"' in page.text
    assert 'data-service="gateway"' in page.text
    assert '/control/assets/control.css' in page.text
    assert '/control/assets/control.js' in page.text


def test_control_rest_serves_console_assets_with_python_sdk_pairing_contract() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    stylesheet = client.get("/control/assets/control.css")
    script = client.get("/control/assets/control.js")

    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "prefers-reduced-motion" in stylesheet.text
    assert ":focus-visible" in stylesheet.text

    assert script.status_code == 200
    assert "application/javascript" in script.headers["content-type"]
    assert 'target_mode: "python_sdk"' in script.text
    assert '"/daemon/devices/pair"' in script.text
    assert '"/daemon/devices/pair/cancel"' in script.text
    assert '"/daemon/devices/disconnect"' in script.text
    assert '"/daemon/status"' in script.text
    assert '"/daemon/devices"' in script.text
    assert "setInterval" in script.text


def test_control_rest_selects_application_and_requests_runtime_shutdown() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    selected = client.post(
        "/daemon/application/select",
        json={"application_dir": "C:/apps/demo"},
    )
    stopped = client.post("/daemon/stop")

    assert selected.status_code == 200
    assert selected.json()["application"]["current_app"] == "selected_app"
    assert controller.selected_application_dir == "C:/apps/demo"
    assert stopped.status_code == 202
    assert controller.shutdown_requested is True
