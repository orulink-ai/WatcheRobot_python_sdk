"""Minimal REST control plane for the Daemon-owned Application lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from watcherobot.application.catalog import (
    ApplicationCatalogError,
    CatalogNotFoundError,
    CatalogPackageError,
)
from watcherobot.runtime.daemon.application.runtime import ApplicationStartError
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
)
from watcherobot.runtime.daemon.application.session import SessionOccupiedError
from watcherobot.runtime.daemon.pairing.session import PairingSessionError


_CONTROL_CONSOLE_DIR = Path(__file__).with_name("static")
_CONTROL_CONSOLE_CSP = (
    "default-src 'self'; "
    "connect-src 'self' http://127.0.0.1:3101 http://localhost:3101; "
    "img-src 'self' data:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'"
)


class PairDeviceRequest(BaseModel):
    pairing_code: str
    target_mode: str


class SelectApplicationRequest(BaseModel):
    application_dir: str


class InstallApplicationRequest(BaseModel):
    package_path: str


class SelectCatalogApplicationRequest(BaseModel):
    app_id: str
    version: str | None = None


class UninstallApplicationRequest(BaseModel):
    app_id: str
    version: str | None = None


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
    ) -> dict[str, Any]:
        """Start pairing the only device slot."""

    async def cancel_device_pairing(self) -> dict[str, Any]:
        """Cancel the current discovery or connection attempt."""

    async def disconnect_device(self) -> bool:
        """Explicitly release the Daemon-owned device session."""

    def select_application(self, application_dir: str) -> dict[str, Any]:
        """Select a validated Application without restarting the Runtime."""

    def request_shutdown(self) -> None:
        """Ask the owning Runtime process to stop cleanly."""

    def daemon_logs(self, after_id: int = 0) -> list[dict[str, Any]]:
        """Return current-session Daemon logs newer than ``after_id``."""

    def list_catalog_applications(self) -> list[dict[str, Any]]:
        """List installed Application versions."""

    def install_application_package(
        self,
        package_path: str,
    ) -> dict[str, Any]:
        """Install one validated .wapp package."""

    def select_catalog_application(
        self,
        app_id: str,
        version: str | None,
    ) -> dict[str, Any]:
        """Select one installed Application version."""

    def uninstall_catalog_application(
        self,
        app_id: str,
        version: str | None,
    ) -> None:
        """Remove one non-protected installed Application."""

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
        app.mount(
            "/control/assets",
            StaticFiles(directory=_CONTROL_CONSOLE_DIR),
            name="control-console-assets",
        )

        @app.get("/control", include_in_schema=False)
        async def redirect_control_console() -> RedirectResponse:
            return RedirectResponse(url="/control/")

        @app.get("/control/", include_in_schema=False)
        async def get_control_console() -> FileResponse:
            return FileResponse(
                _CONTROL_CONSOLE_DIR / "index.html",
                media_type="text/html",
                headers={
                    "Cache-Control": "no-store",
                    "Content-Security-Policy": _CONTROL_CONSOLE_CSP,
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                },
            )

        @app.get("/daemon/status")
        async def get_status() -> dict[str, Any]:
            return self._status_response()

        @app.get("/daemon/logs")
        async def get_logs(after_id: int = 0) -> dict[str, Any]:
            return {"logs": self._controller.daemon_logs(after_id)}

        @app.post("/daemon/application/start")
        async def start_application() -> Any:
            try:
                await self._controller.start_application()
            except SessionOccupiedError as exc:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "application_occupied",
                        "message": str(exc),
                    },
                )
            except ApplicationStartError as exc:
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "application_start_failed",
                        "message": str(exc),
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
                self._controller.select_application(request.application_dir)
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
            return self._status_response()

        @app.post("/daemon/stop", status_code=202)
        async def stop_daemon() -> dict[str, bool]:
            self._controller.request_shutdown()
            return {"stopping": True}

        @app.get("/daemon/applications")
        async def list_applications() -> dict[str, Any]:
            return {
                "applications": self._controller.list_catalog_applications()
            }

        @app.post("/daemon/applications/install", status_code=201)
        async def install_application(
            request: InstallApplicationRequest,
        ) -> Any:
            try:
                installed = self._controller.install_application_package(
                    request.package_path
                )
            except ApplicationCatalogError as exc:
                return _catalog_error_response(exc)
            return {"application": installed}

        @app.post("/daemon/applications/select")
        async def select_catalog_application(
            request: SelectCatalogApplicationRequest,
        ) -> Any:
            try:
                self._controller.select_catalog_application(
                    request.app_id,
                    request.version,
                )
            except ApplicationCatalogError as exc:
                return _catalog_error_response(exc)
            return self._status_response()

        @app.post("/daemon/applications/uninstall")
        async def uninstall_application(
            request: UninstallApplicationRequest,
        ) -> Any:
            try:
                self._controller.uninstall_catalog_application(
                    request.app_id,
                    request.version,
                )
            except ApplicationCatalogError as exc:
                return _catalog_error_response(exc)
            return {"uninstalled": True}

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

        return app

    def _status_response(self) -> dict[str, Any]:
        return {"application": self._controller.application_status()}


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


def _catalog_error_response(error: ApplicationCatalogError) -> JSONResponse:
    if isinstance(error, CatalogNotFoundError):
        status_code = 404
    elif isinstance(error, CatalogPackageError):
        status_code = 400
    else:
        status_code = 409
    return JSONResponse(
        status_code=status_code,
        content={
            "error": type(error).__name__,
            "message": str(error),
        },
    )
