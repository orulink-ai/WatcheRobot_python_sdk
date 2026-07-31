"""Composition root for Daemon connection and Application runtime modules."""

from __future__ import annotations

import json
import asyncio
import secrets
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from watcherobot.application.catalog import (
    ApplicationCatalog,
    CatalogEntry,
)
from watcherobot.runtime.daemon.application.logging import ApplicationLogService
from watcherobot.runtime.daemon.application.runtime import (
    ApplicationRuntimeError,
    ApplicationRuntimeManager,
)
from watcherobot.runtime.daemon.application.session import ApplicationRun
from watcherobot.runtime.daemon.connections.registry import ExternalConnectionRegistry
from watcherobot.runtime.daemon.connections.registry import ExternalClientRole
from watcherobot.runtime.daemon.connections.websocket_server import ExternalWebSocketServer
from watcherobot.runtime.daemon.control.rest import DaemonControlServer
from watcherobot.runtime.daemon.logging import DaemonLogService
from watcherobot.runtime.daemon.pairing.protocol import (
    DeviceSessionEnd,
    HardwareHello,
    build_device_state_event,
)
from watcherobot.runtime.daemon.pairing.session import DevicePairingSession, DevicePairingState
from watcherobot.runtime.daemon.pairing.udp import PairingUdpService
from watcherobot.runtime.daemon.preview.face_tracking import (
    FaceTrackingPreviewBroker,
)
from watcherobot.runtime.daemon.routing.raw import RawFrameRouter


class DaemonRuntime:
    """Own long-lived external connections and the current Application."""

    def __init__(
        self,
        *,
        application_dir: Path,
        current_app: str,
        python_executable: str = sys.executable,
        external_host: str = "0.0.0.0",
        external_port: int = 8765,
        control_host: str = "127.0.0.1",
        control_port: int = 8767,
        auto_start_application: bool = False,
        application_log_dir: Path | None = None,
        daemon_log_path: Path | None = None,
        pairing_udp_port: int = 37021,
        catalog_root: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        connection_registry = ExternalConnectionRegistry()
        self.logs = DaemonLogService(
            log_path=(
                Path(daemon_log_path)
                if daemon_log_path is not None
                else Path(application_dir) / "logs" / "daemon.jsonl"
            )
        )
        self.application_logs = ApplicationLogService(
            log_dir=(
                Path(application_log_dir)
                if application_log_dir is not None
                else Path(application_dir) / "logs" / "applications"
            ),
            desktop_forwarder=lambda frame: connection_registry.send_to_role(
                ExternalClientRole.DESKTOP,
                frame,
            ),
        )
        self.application = ApplicationRuntimeManager(
            application_dir=application_dir,
            current_app=current_app,
            python_executable=python_executable,
            log_service=self.application_logs,
        )
        self.catalog = ApplicationCatalog(
            catalog_root or Path(application_dir).resolve().parent / "catalog",
            is_runtime_active=lambda: (
                self.application.registry.active_run is not None
            ),
        )
        self.router = RawFrameRouter(
            connection_registry,
            application_registry=self.application.registry,
            application_bridge=self.application.bridge,
        )
        self.application.bridge.set_frame_callback(
            self.router.route_application
        )
        self.device_pairing = DevicePairingSession(
            daemon_instance_id=secrets.token_hex(16),
        )
        self._clock = clock
        self.connection_registry = connection_registry
        self.face_tracking_preview = FaceTrackingPreviewBroker(
            connection_registry
        )
        self.external_server = ExternalWebSocketServer(
            host=external_host,
            port=external_port,
            registry=connection_registry,
            router=self.router,
            hardware_hello_authorizer=self._authorize_hardware_hello,
            device_disconnect_listener=self._device_disconnected,
            device_session_end_listener=self._device_session_ended,
            business_frame_listener=self.face_tracking_preview.observe_frame,
            external_disconnect_listener=(
                self.face_tracking_preview.connection_lost
            ),
        )
        self.pairing_udp = PairingUdpService(
            session=self.device_pairing,
            port=pairing_udp_port,
            clock=clock,
            state_listener=self._publish_device_state,
            event_logger=self.logs.record,
        )
        self.control_server = DaemonControlServer(
            controller=self,
            host=control_host,
            port=control_port,
        )
        self._auto_start_enabled = auto_start_application
        self.auto_start_attempted = False
        self.auto_start_error: str | None = None
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        self.logs.record("Daemon Runtime starting")
        await self.external_server.start()
        try:
            await self.pairing_udp.start()
        except Exception:
            await self.external_server.stop()
            raise
        try:
            await self.control_server.start()
        except Exception:
            await self.pairing_udp.stop()
            await self.external_server.stop()
            raise
        self.logs.record(
            "Daemon Runtime ready "
            f"(external={self.external_server.url}, "
            f"control={self.control_server.base_url}, "
            f"pairing_udp={self.pairing_udp.bound_port})"
        )
        if self._auto_start_enabled and not self.auto_start_attempted:
            self.auto_start_attempted = True
            try:
                await self.start_application()
            except ApplicationRuntimeError as exc:
                self.auto_start_error = str(exc)

    async def stop(self) -> None:
        self.logs.record("Daemon Runtime stopping")
        await self.control_server.stop()
        await self.application.stop()
        await self.pairing_udp.stop()
        await self.external_server.stop()

    async def start_application(self) -> ApplicationRun:
        self.logs.record(
            "Application start requested "
            f"(app_id={self.application.registry.current_app})"
        )
        try:
            run = await self.application.start()
        except Exception as exc:
            self.logs.record(f"Application start failed ({exc})")
            raise
        self.logs.record(
            "Application running "
            f"(app_id={run.app_id}, pid={self.application.process_id})"
        )
        return run

    async def stop_application(self) -> None:
        app_id = self.application.registry.current_app
        self.logs.record(f"Application stop requested (app_id={app_id})")
        await self.application.stop()
        self.logs.record(f"Application stopped (app_id={app_id})")

    def select_application(self, application_dir: str) -> dict[str, object]:
        self.application.select_application(Path(application_dir))
        self.logs.record(
            "Application selected "
            f"(app_id={self.application.registry.current_app}, "
            f"path={Path(application_dir).resolve()})"
        )
        return self.application_status()

    def list_catalog_applications(self) -> list[dict[str, object]]:
        return [_catalog_entry_payload(entry) for entry in self.catalog.list()]

    def install_application_package(
        self,
        package_path: str,
    ) -> dict[str, object]:
        entry = self.catalog.install(Path(package_path))
        return _catalog_entry_payload(entry)

    def select_catalog_application(
        self,
        app_id: str,
        version: str | None,
    ) -> dict[str, object]:
        entry = self.catalog.select(app_id, version=version)
        self.application.select_application(entry.path)
        return self.application_status()

    def uninstall_catalog_application(
        self,
        app_id: str,
        version: str | None,
    ) -> None:
        self.catalog.uninstall(app_id, version=version)

    def request_shutdown(self) -> None:
        self.logs.record("Daemon shutdown requested")
        self._shutdown_event.set()

    def daemon_logs(self, after_id: int = 0) -> list[dict[str, object]]:
        return [
            dict(event)
            for event in self.logs.recent(after_id=after_id)
        ]

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()

    async def pair_device(
        self,
        pairing_code: str,
        target_mode: str,
    ) -> dict[str, object]:
        """Reserve the single slot and start Daemon-owned UDP discovery."""

        self.device_pairing.start_pairing(
            pairing_code=pairing_code,
            target_mode=target_mode,
            websocket_port=self.external_server.bound_port,
            now=self._clock(),
        )
        try:
            self.pairing_udp.activate()
        except Exception:
            self.device_pairing.release()
            raise
        request = self.device_pairing.current_request
        assert request is not None
        self.logs.record(
            "Device pairing started "
            f"(mode={target_mode}, request_id={request.request_id})"
        )
        await self._publish_device_state(self.device_pairing.snapshot())
        return self.device_status()

    async def cancel_device_pairing(self) -> dict[str, object]:
        await self.pairing_udp.cancel_pairing()
        self.logs.record("Device pairing cancelled")
        return self.device_status()

    async def disconnect_device(self) -> bool:
        """Release the single device session without involving Application."""

        state = self.device_pairing.state
        if state is DevicePairingState.IDLE:
            self.logs.record("Device disconnect ignored (no active session)")
            return False
        if state in {
            DevicePairingState.DISCOVERING,
            DevicePairingState.CONNECTING,
        }:
            await self.pairing_udp.cancel_pairing()
            self.logs.record("Device pairing cancelled by disconnect request")
            return True

        request = self.device_pairing.current_request
        if state is DevicePairingState.CONNECTED and request is not None:
            await self.connection_registry.send_to_role(
                ExternalClientRole.DEVICE,
                json.dumps(
                    {
                        "type": "sys.device.session.close",
                        "code": 0,
                        "data": {
                            "pair_request_id": request.request_id,
                            "reason": "disconnect_requested",
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        self.device_pairing.release()
        await self.connection_registry.close_role(
            ExternalClientRole.DEVICE,
            code=1000,
            reason="device session released",
        )
        await self._publish_device_state(self.device_pairing.snapshot())
        self.logs.record("Device disconnected by desktop request")
        return True

    def application_status(self) -> dict[str, object]:
        run = self.application.registry.active_run
        state = run.state if run is not None else self.application.last_state
        return {
            "current_app": self.application.registry.current_app,
            "state": state.value,
            "process_id": self.application.process_id,
            "last_exit_code": self.application.last_exit_code,
        }

    def device_status(self) -> dict[str, object]:
        device = self.device_pairing.snapshot()
        peer_ip = self.device_pairing.expected_peer_ip
        device["preview_websocket_url"] = (
            f"ws://{peer_ip}:81/ws/face-track"
            if device["online"] and peer_ip is not None
            else None
        )
        return {"device": device}

    async def _authorize_hardware_hello(
        self,
        hello: HardwareHello,
        peer_ip: str,
    ) -> None:
        self.device_pairing.connect_device(
            hello,
            peer_ip=peer_ip,
            now=self._clock(),
        )
        self.logs.record(
            "Device connected "
            f"(peer_ip={peer_ip}, request_id={hello.pair_request_id})"
        )
        await self._publish_device_state(self.device_pairing.snapshot())

    async def _device_disconnected(self, _peer_ip: str) -> None:
        if self.device_pairing.state is not DevicePairingState.CONNECTED:
            return
        self.device_pairing.device_disconnected(now=self._clock())
        self.logs.record(f"Device connection lost (peer_ip={_peer_ip})")
        self.pairing_udp.activate()
        await self._publish_device_state(self.device_pairing.snapshot())

    async def _device_session_ended(
        self,
        message: DeviceSessionEnd,
        _peer_ip: str,
    ) -> None:
        self.device_pairing.end_device_session(
            pair_request_id=message.pair_request_id,
        )
        self.logs.record(
            "Device session ended "
            f"(request_id={message.pair_request_id}, reason={message.reason})"
        )
        await self._publish_device_state(self.device_pairing.snapshot())

    async def _publish_device_state(
        self,
        snapshot: Mapping[str, object],
    ) -> None:
        await self.connection_registry.send_to_role(
            ExternalClientRole.DESKTOP,
            json.dumps(
                build_device_state_event(snapshot),
                separators=(",", ":"),
            ),
        )


def _catalog_entry_payload(entry: CatalogEntry) -> dict[str, object]:
    return {
        "id": entry.app_id,
        "name": entry.name,
        "version": entry.version,
        "path": str(entry.path),
        "requires_watcherobot": entry.requires_watcherobot,
    }
