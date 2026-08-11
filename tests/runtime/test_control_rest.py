from __future__ import annotations

import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from watcherobot.runtime.daemon.application.launcher import ApplicationLaunchError
from watcherobot.runtime.daemon.application.runtime import ApplicationStartError
from watcherobot.runtime.daemon.application.session import (
    ApplicationNotSelectedError,
    ApplicationRun,
    ApplicationState,
    SessionOccupiedError,
)
from watcherobot.runtime.daemon.control.rest import DaemonControlAPI, DaemonControlServer
from watcherobot.runtime.daemon.maintenance import MaintenanceError
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
        self.work_requests: list[tuple[dict, str, str]] = []
        self.work_read_requests: list[dict] = []
        self.maintenance_requests: list[dict] = []
        self.active_maintenance = {"id": "firmware-job", "kind": "firmware", "status": "running"}

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

    def start_maintenance_work(
        self,
        composition: dict | None,
        package_path: str,
        port: str,
        *,
        transport: str = "serial",
        volume_id: str = "",
    ) -> dict:
        self.work_requests.append((composition, package_path, port, transport, volume_id))
        return {"id": "work-job", "kind": "work", "status": "queued"}

    def maintenance_ports(self) -> list[dict]:
        return [{"device": "COM29", "description": "USB", "hwid": "USB", "vid": 1, "pid": 2}]

    def maintenance_releases(self, kind: str) -> list[dict]:
        return [{"version": "v0.0.8", "name": kind, "published_at": "", "prerelease": False, "assets": []}]

    def maintenance_volumes(self) -> list[dict]:
        return [{"id": "E:\\|1234", "root": "E:\\", "current_version": "v0.0.7"}]

    def format_maintenance_volume_as_fat32(self, volume_id: str) -> dict:
        self.formatted_volume_id = volume_id
        return {"id": "E:\\|5678", "root": "E:\\", "filesystem": "FAT32"}

    def validate_maintenance_package(self, kind: str, package_path: str) -> dict:
        if package_path == "invalid.zip":
            raise MaintenanceError("固件 ZIP 缺少 flash_args.txt。")
        return {"kind": kind, "package_path": package_path}

    def maintenance_device_info(self, port: str) -> dict:
        return {"port": port, "firmware_version": "V2.4.1", "sd_version": "v0.0.8"}

    def read_maintenance_work(
        self,
        *,
        transport: str,
        work_id: str,
        port: str = "",
        volume_id: str = "",
    ) -> dict:
        self.work_read_requests.append({
            "transport": transport,
            "work_id": work_id,
            "port": port,
            "volume_id": volume_id,
        })
        return {
            "id": work_id,
            "name": "SD 作品",
            "composition": {"kind": "watcher.creator-composition", "clips": []},
        }

    def start_maintenance_job(
        self,
        kind: str,
        package_path: str,
        port: str,
        **options,
    ) -> dict:
        self.maintenance_requests.append({"kind": kind, "package_path": package_path, "port": port, **options})
        return {"id": "maintenance-job", "kind": kind, "status": "queued"}

    def maintenance_job(self, job_id: str) -> dict:
        return {"id": job_id, "kind": "firmware", "status": "running"}

    def active_maintenance_job(self) -> dict | None:
        return self.active_maintenance


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

    controller.start_error = ApplicationNotSelectedError(
        "No Application is selected"
    )
    unselected = client.post("/daemon/application/start")
    assert unselected.status_code == 409
    assert unselected.json() == {
        "error": "application_not_selected",
        "message": "No Application is selected",
    }

    controller.start_error = ApplicationStartError("entrypoint failed")
    failed = client.post("/daemon/application/start")
    assert failed.status_code == 500
    assert failed.json()["error"] == "application_start_failed"


def test_control_rest_starts_creator_work_install() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    response = client.post("/daemon/maintenance/work", json={
        "composition": {"name": "Demo", "clips": []},
        "port": "COM29",
        "transport": "serial",
    })

    assert response.status_code == 202
    assert response.json()["job"]["id"] == "work-job"
    assert controller.work_requests == [(
        {"name": "Demo", "clips": []}, "", "COM29", "serial", "",
    )]


def test_control_rest_reads_an_editable_work_from_sd() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    response = client.post("/daemon/maintenance/works/read", json={
        "transport": "card_reader",
        "volume_id": "E:\\|1234",
        "work_id": "demo_work",
    })

    assert response.status_code == 202
    assert response.json()["work"]["id"] == "demo_work"
    assert controller.work_read_requests == [{
        "transport": "card_reader",
        "work_id": "demo_work",
        "port": "",
        "volume_id": "E:\\|1234",
    }]


def test_control_rest_returns_active_maintenance_job() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    response = client.get("/daemon/maintenance/jobs/active")

    assert response.status_code == 202
    assert response.json() == {"job": controller.active_maintenance}

    controller.active_maintenance = None
    assert client.get("/daemon/maintenance/jobs/active").json() == {"job": None}


def test_control_rest_adds_release_reader_and_device_info_without_breaking_local_install() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    assert client.get("/daemon/maintenance/releases/firmware").status_code == 200
    assert client.get("/daemon/maintenance/volumes").json()["volumes"][0]["current_version"] == "v0.0.7"
    assert client.post("/daemon/maintenance/device-info", json={"port": "COM29"}).json()["device"]["firmware_version"] == "V2.4.1"

    legacy = client.post("/daemon/maintenance/firmware", json={"package_path": "firmware.zip", "port": "COM29"})
    assert legacy.status_code == 202
    assert controller.maintenance_requests[-1]["transport"] == "serial"

    reader = client.post("/daemon/maintenance/sd-resources", json={
        "package_path": "resources.tar.gz",
        "transport": "card_reader",
        "volume_id": "E:\\|1234",
    })
    assert reader.status_code == 202
    assert controller.maintenance_requests[-1]["volume_id"] == "E:\\|1234"


def test_control_rest_formats_only_the_explicitly_selected_volume() -> None:
    controller = _ControllerStub()
    client = TestClient(DaemonControlAPI(controller=controller).create_app())

    response = client.post("/daemon/maintenance/volumes/format-fat32", json={"volume_id": "E:\\|1234"})

    assert response.status_code == 202
    assert controller.formatted_volume_id == "E:\\|1234"
    assert response.json()["job"]["filesystem"] == "FAT32"


def test_control_rest_validates_local_package_before_install() -> None:
    client = TestClient(DaemonControlAPI(controller=_ControllerStub()).create_app())

    valid = client.post("/daemon/maintenance/packages/validate", json={
        "kind": "firmware",
        "package_path": "firmware.zip",
    })
    invalid = client.post("/daemon/maintenance/packages/validate", json={
        "kind": "firmware",
        "package_path": "invalid.zip",
    })

    assert valid.status_code == 200
    assert valid.json()["package"]["kind"] == "firmware"
    assert invalid.status_code == 400
    assert "flash_args.txt" in invalid.json()["message"]


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


def test_slow_maintenance_io_does_not_block_daemon_status() -> None:
    async def scenario() -> None:
        controller = _ControllerStub()

        def slow_works(**_kwargs: object) -> list[dict]:
            time.sleep(0.4)
            return []

        controller.maintenance_works = slow_works  # type: ignore[method-assign]
        server = DaemonControlServer(
            controller=controller,
            host="127.0.0.1",
            port=0,
        )
        await server.start()
        try:
            async with httpx.AsyncClient() as client:
                started = time.monotonic()
                works_request = asyncio.create_task(client.post(
                    f"{server.base_url}/daemon/maintenance/works/list",
                    json={"transport": "card_reader", "volume_id": "E:\\\\|1234"},
                ))
                await asyncio.sleep(0.05)
                status = await client.get(f"{server.base_url}/daemon/status")
                status_elapsed = time.monotonic() - started
                works = await works_request
            assert status.status_code == 200
            assert status_elapsed < 0.25
            assert works.status_code == 200
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
