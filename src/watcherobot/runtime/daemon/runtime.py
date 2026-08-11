"""Composition root for Daemon connection and Application runtime modules."""

from __future__ import annotations

import json
import asyncio
import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from watcherobot.runtime.daemon.application.bridge import ChannelNotConnectedError
from watcherobot.runtime.daemon.application.logging import ApplicationLogService
from watcherobot.runtime.daemon.application.launcher import ApplicationLauncher
from watcherobot.runtime.daemon.application.runtime import (
    ApplicationRuntimeError,
    ApplicationRuntimeManager,
)
from watcherobot.runtime.daemon.application.session import (
    ApplicationChannel,
    ApplicationRun,
)
from watcherobot.runtime.daemon.connections.registry import (
    ExternalClientRole,
    ExternalConnectionRegistry,
)
from watcherobot.runtime.daemon.connections.websocket_server import (
    ExternalWebSocketServer,
)
from watcherobot.runtime.daemon.control.rest import DaemonControlServer
from watcherobot.runtime.daemon.logging import DaemonLogService
from watcherobot.runtime.daemon.maintenance import MaintenanceError, MaintenanceService
from watcherobot.runtime.daemon.pairing.protocol import (
    DeviceSessionEnd,
    HardwareHello,
    build_device_state_event,
)
from watcherobot.runtime.daemon.pairing.session import (
    DevicePairingSession,
    DevicePairingState,
)
from watcherobot.runtime.daemon.pairing.udp import PairingUdpService
from watcherobot.runtime.daemon.preview.face_tracking import (
    FaceTrackingPreviewBroker,
)
from watcherobot.runtime.daemon.preview.udp_service import (
    FaceTrackingUdpPreviewService,
)
from watcherobot.runtime.daemon.routing.raw import RawFrameRouter


class DaemonRuntime:
    """Own long-lived external connections and the current Application."""

    def __init__(
        self,
        *,
        application_dir: Path,
        current_app: str | None,
        external_host: str = "0.0.0.0",
        external_port: int = 8765,
        control_host: str = "127.0.0.1",
        control_port: int = 8767,
        auto_start_application: bool = False,
        application_log_dir: Path | None = None,
        daemon_log_path: Path | None = None,
        pairing_udp_port: int = 37021,
        preview_udp_port: int = 0,
        managed_app_root: Path | None = None,
        bundled_resource_root: Path | None = None,
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
        application_parent = Path(application_dir).resolve().parent
        application_launcher = ApplicationLauncher(
            managed_app_root=managed_app_root or application_parent,
            bundled_resource_root=bundled_resource_root or application_parent,
        )
        self.application = ApplicationRuntimeManager(
            application_dir=application_dir,
            current_app=current_app,
            application_launcher=application_launcher,
            log_service=self.application_logs,
        )
        self.router = RawFrameRouter(
            connection_registry,
            application_registry=self.application.registry,
            application_bridge=self.application.bridge,
        )
        self.device_pairing = DevicePairingSession(
            daemon_instance_id=secrets.token_hex(16),
        )
        self._clock = clock
        self.connection_registry = connection_registry
        self.face_tracking_preview = FaceTrackingPreviewBroker(connection_registry)
        self.application.bridge.set_frame_callback(
            self._route_application_frame
        )
        self.application.bridge.add_channel_lost_listener(
            self.face_tracking_preview.application_channel_lost
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
            external_disconnect_listener=(self.face_tracking_preview.connection_lost),
        )
        self.pairing_udp = PairingUdpService(
            session=self.device_pairing,
            port=pairing_udp_port,
            clock=clock,
            state_listener=self._publish_device_state,
            event_logger=self.logs.record,
        )
        self.preview_udp = FaceTrackingUdpPreviewService(
            session=self.device_pairing,
            registry=connection_registry,
            publisher=self._publish_preview_frame,
            port=preview_udp_port,
        )
        self.maintenance = MaintenanceService()
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
            await self.preview_udp.start()
        except Exception:
            await self.pairing_udp.stop()
            await self.external_server.stop()
            raise
        try:
            await self.control_server.start()
        except Exception:
            await self.preview_udp.stop()
            await self.pairing_udp.stop()
            await self.external_server.stop()
            raise
        self.logs.record(
            "Daemon Runtime ready "
            f"(external={self.external_server.url}, "
            f"control={self.control_server.base_url}, "
            f"pairing_udp={self.pairing_udp.bound_port}, "
            f"preview_udp={self.preview_udp.bound_port})"
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
        await self.preview_udp.stop()
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

    def select_application(
        self,
        application_dir: str,
        launcher_kind: str,
        launcher_executable: str,
    ) -> dict[str, object]:
        self.application.select_application(
            Path(application_dir),
            launcher_kind=launcher_kind,
            launcher_executable=Path(launcher_executable),
        )
        self.logs.record(
            "Application selected "
            f"(app_id={self.application.registry.current_app}, "
            f"path={Path(application_dir).resolve()}, "
            f"launcher={launcher_kind})"
        )
        return self.application_status()

    def request_shutdown(self) -> None:
        self.logs.record("Daemon shutdown requested")
        self._shutdown_event.set()

    def daemon_logs(self, after_id: int = 0) -> list[dict[str, object]]:
        return [dict(event) for event in self.logs.recent(after_id=after_id)]

    def maintenance_ports(self) -> list[dict[str, object]]:
        return self.maintenance.ports()

    def maintenance_releases(self, kind: str) -> list[dict[str, object]]:
        try:
            return self.maintenance.releases(kind)
        except Exception as exc:
            raise MaintenanceError(str(exc)) from exc

    def maintenance_volumes(self) -> list[dict[str, object]]:
        return self.maintenance.volumes()

    def format_maintenance_volume_as_fat32(self, volume_id: str) -> dict[str, object]:
        return self.maintenance.format_volume_as_fat32(volume_id)

    def validate_maintenance_package(self, kind: str, package_path: str) -> dict[str, object]:
        return self.maintenance.validate_package(kind, package_path)

    def maintenance_device_info(self, port: str) -> dict[str, object]:
        return self.maintenance.device_info(port)

    def maintenance_works(
        self,
        *,
        transport: str,
        port: str = "",
        volume_id: str = "",
    ) -> list[dict[str, object]]:
        return self.maintenance.works(transport=transport, port=port, volume_id=volume_id)

    def read_maintenance_work(
        self,
        *,
        transport: str,
        work_id: str,
        port: str = "",
        volume_id: str = "",
    ) -> dict[str, object]:
        return self.maintenance.read_work(
            transport=transport,
            work_id=work_id,
            port=port,
            volume_id=volume_id,
        )

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
    ) -> dict[str, object]:
        return self.maintenance.start(
            kind,
            package_path,
            port,
            transport=transport,
            volume_id=volume_id,
            release_version=release_version,
            release_asset=release_asset,
        )

    def maintenance_job(self, job_id: str) -> dict[str, object]:
        return self.maintenance.get(job_id)

    def active_maintenance_job(self) -> dict[str, object] | None:
        return self.maintenance.active()

    def start_maintenance_work(
        self,
        composition: dict[str, object] | None,
        package_path: str,
        port: str,
        *,
        transport: str = "serial",
        volume_id: str = "",
    ) -> dict[str, object]:
        return self.maintenance.start_work(
            composition,
            package_path,
            port,
            transport=transport,
            volume_id=volume_id,
        )

    def export_maintenance_work(self, composition: dict[str, object]) -> dict[str, object]:
        return self.maintenance.export_work_package(composition)

    def import_maintenance_work(self, package_path: str) -> dict[str, object]:
        return self.maintenance.import_work_package(package_path)

    def delete_maintenance_work(
        self,
        *,
        transport: str,
        work_id: str,
        port: str = "",
        volume_id: str = "",
    ) -> None:
        self.maintenance.delete_work(
            transport=transport,
            work_id=work_id,
            port=port,
            volume_id=volume_id,
        )

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
            "selected": self.application.registry.current_app is not None,
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
        device["preview_transport"] = self.preview_udp.snapshot()
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

    async def _route_application_frame(
        self,
        source: ApplicationChannel,
        frame: str | bytes,
    ) -> int:
        delivered = await self.router.route_application(source, frame)
        if delivered > 0:
            await self.face_tracking_preview.observe_application_frame(
                source,
                frame,
            )
        return delivered

    async def _publish_preview_frame(self, frame: str | bytes) -> int:
        if self.application.registry.active_run is not None:
            try:
                await self.application.bridge.send_to_application(
                    ApplicationChannel.DEVICE,
                    frame,
                )
            except ChannelNotConnectedError:
                return 0
            return 1
        return await self.connection_registry.send_to_role(
            ExternalClientRole.DESKTOP,
            frame,
        )
