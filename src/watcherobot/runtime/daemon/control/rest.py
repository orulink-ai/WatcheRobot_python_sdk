"""Minimal REST control plane for the Daemon-owned Application lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from ipaddress import IPv4Address
from typing import Any, Protocol

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from watcherobot import __version__
from watcherobot.runtime.daemon.application.runtime import ApplicationStartError
from watcherobot.runtime.daemon.application.launcher import ApplicationLaunchError
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
)
from watcherobot.runtime.daemon.application.session import (
    ApplicationNotSelectedError,
    SessionOccupiedError,
)
from watcherobot.runtime.daemon.pairing.session import PairingSessionError
from watcherobot.runtime.daemon.maintenance import MaintenanceError


DAEMON_CONTROL_PROTOCOL_VERSION = 2


class PairDeviceRequest(BaseModel):
    pairing_code: str
    target_mode: str
    device_ip: IPv4Address | None = None


class ApplicationLauncherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    executable: str


class SelectApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_dir: str
    launcher: ApplicationLauncherRequest


class MaintenanceInstallRequest(BaseModel):
    package_path: str = ""
    port: str = ""
    transport: str = "serial"
    volume_id: str = ""
    release_version: str = ""
    release_asset: str = ""


class MaintenanceDeviceInfoRequest(BaseModel):
    port: str


class MaintenancePackageValidationRequest(BaseModel):
    kind: str
    package_path: str


class MaintenanceWorkRequest(BaseModel):
    composition: dict[str, Any] | None = None
    package_path: str = ""
    port: str = ""
    transport: str = "serial"
    volume_id: str = ""


class MaintenanceWorkPackageRequest(BaseModel):
    package_path: str


class MaintenanceWorkExportRequest(BaseModel):
    composition: dict[str, Any]


class MaintenanceWorkDeleteRequest(BaseModel):
    transport: str = "serial"
    work_id: str
    port: str = ""
    volume_id: str = ""


class MaintenanceWorksQuery(BaseModel):
    transport: str = "serial"
    port: str = ""
    volume_id: str = ""


class MaintenanceWorkReadRequest(MaintenanceWorksQuery):
    work_id: str


class ApplicationController(Protocol):
    def application_status(self) -> dict[str, Any]:
        """Return a serializable snapshot of the current Application."""

    async def start_application(self) -> Any:
        """Start the selected Application."""

    async def stop_application(self) -> None:
        """Stop the current Application if one exists."""

    def device_status(self) -> dict[str, Any]:
        """Return the Daemon-owned single device slot."""

    async def pair_device(
        self,
        pairing_code: str,
        target_mode: str,
        device_ip: str | None = None,
    ) -> dict[str, Any]:
        """Start pairing the only device slot."""

    async def cancel_device_pairing(self) -> dict[str, Any]:
        """Cancel the current discovery or connection attempt."""

    async def disconnect_device(self) -> bool:
        """Explicitly release the Daemon-owned device session."""

    def select_application(
        self,
        application_dir: str,
        launcher_kind: str,
        launcher_executable: str,
    ) -> dict[str, Any]:
        """Select a validated Application without restarting the Runtime."""

    def request_shutdown(self) -> None:
        """Ask the owning Runtime process to stop cleanly."""

    def daemon_logs(self, after_id: int = 0) -> list[dict[str, Any]]:
        """Return current-session Daemon logs newer than ``after_id``."""

    def maintenance_ports(self) -> list[dict[str, Any]]:
        """List local serial ports available for maintenance."""

    def maintenance_releases(self, kind: str) -> list[dict[str, Any]]:
        """List compatible official Release packages."""

    def maintenance_volumes(self) -> list[dict[str, Any]]:
        """List writable Windows SD-card reader volumes."""

    def validate_maintenance_package(self, kind: str, package_path: str) -> dict[str, Any]:
        """Validate a local firmware or SD resource package."""

    def maintenance_device_info(self, port: str) -> dict[str, Any]:
        """Read firmware and SD resource versions from one serial device."""

    def maintenance_works(
        self,
        *,
        transport: str,
        port: str = "",
        volume_id: str = "",
    ) -> list[dict[str, Any]]:
        """Read user works from a device SD card or card reader."""

    def read_maintenance_work(
        self,
        *,
        transport: str,
        work_id: str,
        port: str = "",
        volume_id: str = "",
    ) -> dict[str, Any]:
        """Read one work together with its editable source media."""

    def start_maintenance_job(
        self,
        kind: str,
        package_path: str,
        port: str,
        *,
        transport: str = "serial",
        volume_id: str = "",
        release_version: str = "",
        release_asset: str = "",
    ) -> dict[str, Any]:
        """Start a non-blocking firmware or SD resource job."""

    def maintenance_job(self, job_id: str) -> dict[str, Any]:
        """Return a maintenance job snapshot."""

    def active_maintenance_job(self) -> dict[str, Any] | None:
        """Return the currently running maintenance job, if any."""

    def start_maintenance_work(
        self,
        composition: dict[str, Any] | None,
        package_path: str,
        port: str,
        *,
        transport: str = "serial",
        volume_id: str = "",
    ) -> dict[str, Any]:
        """Build and install the current Creator Mode work."""

    def export_maintenance_work(self, composition: dict[str, Any]) -> dict[str, Any]:
        """Build a portable work ZIP in the local daemon cache."""

    def import_maintenance_work(self, package_path: str) -> dict[str, Any]:
        """Validate and read a local portable work ZIP."""

    def delete_maintenance_work(
        self,
        *,
        transport: str,
        work_id: str,
        port: str = "",
        volume_id: str = "",
    ) -> None:
        """Delete one user work without touching official resources."""

class DaemonControlAPI:
    """Expose lifecycle management without carrying business traffic."""

    def __init__(self, *, controller: ApplicationController) -> None:
        self._controller = controller

    def create_app(self) -> FastAPI:
        app = FastAPI(title="Watcher Daemon Control API")
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=(
                r"^(?:https?://(?:(?:tauri\.)?localhost|127\.0\.0\.1)"
                r"(?::\d+)?|tauri://localhost|null)$"
            ),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
            allow_private_network=True,
        )

        @app.get("/daemon/status")
        async def get_status() -> dict[str, Any]:
            return self._status_response()

        @app.get("/daemon/logs")
        async def get_logs(after_id: int = 0) -> dict[str, Any]:
            return {"logs": self._controller.daemon_logs(after_id)}

        @app.get("/daemon/application/logs")
        async def get_application_logs(limit: int = 100) -> dict[str, Any]:
            return {
                "logs": self._controller.application_log_records(
                    max(1, min(limit, 500))
                )
            }

        @app.post("/daemon/application/start")
        async def start_application() -> Any:
            try:
                await self._controller.start_application()
            except ApplicationNotSelectedError as exc:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "application_not_selected",
                        "message": str(exc),
                    },
                )
            except SessionOccupiedError as exc:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "application_occupied",
                        "message": str(exc),
                    },
                )
            except ApplicationStartError as exc:
                application = self._controller.application_status()
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "application_start_failed",
                        "message": str(exc),
                        "exit_code": application.get("last_exit_code"),
                        "recent_logs": self._controller.application_log_records(20),
                    },
                )
            return self._status_response()

        @app.post("/daemon/application/stop")
        async def stop_application() -> dict[str, Any]:
            await self._controller.stop_application()
            return self._status_response()

        @app.post("/daemon/application/select")
        async def select_application(
            request: SelectApplicationRequest,
        ) -> Any:
            try:
                self._controller.select_application(
                    request.application_dir,
                    request.launcher.kind,
                    request.launcher.executable,
                )
            except SessionOccupiedError as exc:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "application_occupied",
                        "message": str(exc),
                    },
                )
            except ApplicationManifestError as exc:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_application",
                        "message": str(exc),
                    },
                )
            except ApplicationLaunchError as exc:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": exc.code,
                        "message": str(exc),
                    },
                )
            return self._status_response()

        @app.post("/daemon/stop", status_code=202)
        async def stop_daemon() -> dict[str, bool]:
            self._controller.request_shutdown()
            return {"stopping": True}

        @app.get("/daemon/devices")
        async def get_devices() -> dict[str, Any]:
            return self._controller.device_status()

        @app.post("/daemon/devices/pair", status_code=202)
        async def pair_device(
            request: PairDeviceRequest,
        ) -> Any:
            try:
                return await self._controller.pair_device(
                    request.pairing_code,
                    request.target_mode,
                    str(request.device_ip) if request.device_ip is not None else None,
                )
            except PairingSessionError as exc:
                status_code = (
                    409
                    if exc.code in {
                        "device_slot_occupied",
                        "invalid_state_transition",
                    }
                    else 400
                )
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "error": exc.code,
                        "message": str(exc),
                    },
                )

        @app.post("/daemon/devices/pair/cancel")
        async def cancel_device_pairing() -> Any:
            try:
                return await self._controller.cancel_device_pairing()
            except PairingSessionError as exc:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": exc.code,
                        "message": str(exc),
                    },
                )

        @app.post("/daemon/devices/disconnect")
        async def disconnect_device() -> Any:
            disconnected = await self._controller.disconnect_device()
            if not disconnected:
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": "device_not_connected",
                        "message": "璁惧杩炴帴涓嶅瓨鍦ㄦ垨宸叉柇寮€",
                    },
                )
            return {
                "disconnected": True,
                **self._controller.device_status(),
            }

        @app.get("/daemon/maintenance/ports")
        async def maintenance_ports() -> dict[str, Any]:
            ports = await asyncio.to_thread(self._controller.maintenance_ports)
            return {"ports": ports}

        @app.get("/daemon/maintenance/releases/{kind}")
        async def maintenance_releases(kind: str) -> Any:
            try:
                releases = await asyncio.to_thread(self._controller.maintenance_releases, kind)
                return {"releases": releases}
            except MaintenanceError as exc:
                return JSONResponse(status_code=400, content={"error": "release_unavailable", "message": str(exc)})

        @app.get("/daemon/maintenance/volumes")
        async def maintenance_volumes() -> dict[str, Any]:
            volumes = await asyncio.to_thread(self._controller.maintenance_volumes)
            return {"volumes": volumes}

        @app.post("/daemon/maintenance/packages/validate")
        async def validate_maintenance_package(request: MaintenancePackageValidationRequest) -> Any:
            try:
                package = await asyncio.to_thread(
                    self._controller.validate_maintenance_package,
                    request.kind,
                    request.package_path,
                )
                return {"package": package}
            except MaintenanceError as exc:
                return JSONResponse(status_code=400, content={"error": "invalid_package", "message": str(exc)})

        @app.post("/daemon/maintenance/device-info")
        async def maintenance_device_info(request: MaintenanceDeviceInfoRequest) -> Any:
            try:
                device = await asyncio.to_thread(
                    self._controller.maintenance_device_info,
                    request.port,
                )
                return {"device": device}
            except MaintenanceError as exc:
                return JSONResponse(status_code=409, content={"error": "device_info_unavailable", "message": str(exc)})

        @app.post("/daemon/maintenance/firmware", status_code=202)
        async def install_firmware(request: MaintenanceInstallRequest) -> Any:
            return self._start_maintenance("firmware", request)

        @app.post("/daemon/maintenance/sd-resources", status_code=202)
        async def install_sd_resources(request: MaintenanceInstallRequest) -> Any:
            return self._start_maintenance("sd_resources", request)

        @app.post("/daemon/maintenance/work", status_code=202)
        async def install_work(request: MaintenanceWorkRequest) -> Any:
            try:
                job = self._controller.start_maintenance_work(
                    request.composition,
                    request.package_path,
                    request.port,
                    transport=request.transport,
                    volume_id=request.volume_id,
                )
            except MaintenanceError as exc:
                return JSONResponse(status_code=409, content={"error": "maintenance_unavailable", "message": str(exc)})
            return {"job": job}

        @app.post("/daemon/maintenance/works/list")
        async def maintenance_works(request: MaintenanceWorksQuery) -> Any:
            try:
                works = await asyncio.to_thread(
                    self._controller.maintenance_works,
                    transport=request.transport,
                    port=request.port,
                    volume_id=request.volume_id,
                )
            except MaintenanceError as exc:
                return JSONResponse(status_code=409, content={"error": "work_library_unavailable", "message": str(exc)})
            return {"works": works}

        @app.post("/daemon/maintenance/works/read")
        async def read_maintenance_work(request: MaintenanceWorkReadRequest) -> Any:
            try:
                work = await asyncio.to_thread(
                    self._controller.read_maintenance_work,
                    transport=request.transport,
                    work_id=request.work_id,
                    port=request.port,
                    volume_id=request.volume_id,
                )
            except MaintenanceError as exc:
                return JSONResponse(status_code=409, content={"error": "work_read_failed", "message": str(exc)})
            return {"work": work}

        @app.post("/daemon/maintenance/works/export")
        async def export_work(request: MaintenanceWorkExportRequest) -> Any:
            try:
                package = await asyncio.to_thread(
                    self._controller.export_maintenance_work,
                    request.composition,
                )
            except MaintenanceError as exc:
                return JSONResponse(status_code=400, content={"error": "invalid_work", "message": str(exc)})
            return {"package": package}

        @app.post("/daemon/maintenance/works/import")
        async def import_work(request: MaintenanceWorkPackageRequest) -> Any:
            try:
                work = await asyncio.to_thread(
                    self._controller.import_maintenance_work,
                    request.package_path,
                )
            except MaintenanceError as exc:
                return JSONResponse(status_code=400, content={"error": "invalid_work_package", "message": str(exc)})
            return {"work": work}

        @app.post("/daemon/maintenance/works/delete")
        async def delete_work(request: MaintenanceWorkDeleteRequest) -> Any:
            try:
                await asyncio.to_thread(
                    self._controller.delete_maintenance_work,
                    transport=request.transport,
                    work_id=request.work_id,
                    port=request.port,
                    volume_id=request.volume_id,
                )
            except MaintenanceError as exc:
                return JSONResponse(status_code=409, content={"error": "work_delete_failed", "message": str(exc)})
            return {"deleted": True}

        @app.get("/daemon/maintenance/jobs/active")
        async def active_maintenance_job() -> dict[str, Any]:
            return {"job": self._controller.active_maintenance_job()}

        @app.get("/daemon/maintenance/jobs/{job_id}")
        async def maintenance_job(job_id: str) -> Any:
            try:
                return {"job": self._controller.maintenance_job(job_id)}
            except MaintenanceError as exc:
                return JSONResponse(status_code=404, content={"error": "maintenance_job_not_found", "message": str(exc)})

        return app

    def _start_maintenance(self, kind: str, request: MaintenanceInstallRequest) -> Any:
        try:
            job = self._controller.start_maintenance_job(
                kind,
                request.package_path,
                request.port,
                transport=request.transport,
                volume_id=request.volume_id,
                release_version=request.release_version,
                release_asset=request.release_asset,
            )
        except MaintenanceError as exc:
            return JSONResponse(status_code=409, content={"error": "maintenance_unavailable", "message": str(exc)})
        return {"job": job}

    def _status_response(self) -> dict[str, Any]:
        return {
            "runtime": {
                "control_protocol": DAEMON_CONTROL_PROTOCOL_VERSION,
                "sdk_version": __version__,
            },
            "application": self._controller.application_status(),
        }


class DaemonControlServer:
    """Host the Daemon control API on its own local HTTP endpoint."""

    STARTUP_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        controller: ApplicationController,
        host: str = "127.0.0.1",
        port: int = 8767,
    ) -> None:
        self._host = host
        self._requested_port = port
        self._bound_port: int | None = None
        self._api = DaemonControlAPI(controller=controller)
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None

    @property
    def base_url(self) -> str:
        if self._bound_port is None:
            raise RuntimeError("Daemon control server is not running")
        return f"http://{self._host}:{self._bound_port}"

    async def start(self) -> None:
        if self._server_task is not None:
            return
        config = uvicorn.Config(
            app=self._api.create_app(),
            host=self._host,
            port=self._requested_port,
            loop="asyncio",
            http="h11",
            ws="none",
            lifespan="off",
            access_log=False,
            log_level="warning",
            log_config=None,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # type: ignore[attr-defined]
        self._server = server
        self._server_task = asyncio.create_task(
            server.serve(),
            name="daemon-control-rest",
        )
        try:
            await self._wait_until_started()
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        server = self._server
        server_task = self._server_task
        self._server = None
        self._server_task = None
        self._bound_port = None
        if server is not None:
            server.should_exit = True
        if server_task is not None:
            with suppress(asyncio.CancelledError):
                await server_task

    async def _wait_until_started(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.STARTUP_TIMEOUT_SECONDS
        while loop.time() < deadline:
            server = self._server
            server_task = self._server_task
            if server is None or server_task is None:
                raise RuntimeError("Daemon control server stopped during startup")
            if server.started:
                sockets = [
                    sock
                    for listener in server.servers
                    for sock in (listener.sockets or [])
                ]
                if not sockets:
                    raise RuntimeError("Daemon control server has no listening socket")
                self._bound_port = int(sockets[0].getsockname()[1])
                return
            if server_task.done():
                exception = server_task.exception()
                if exception is not None:
                    raise exception
                raise RuntimeError("Daemon control server stopped during startup")
            await asyncio.sleep(0.01)
        raise TimeoutError("Daemon control server startup timed out")
