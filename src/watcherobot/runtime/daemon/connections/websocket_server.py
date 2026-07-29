"""External desktop and hardware WebSocket listener owned by Daemon."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from watcherobot.runtime.daemon.connections.registry import (
    ClientRoleLockedError,
    ExternalClientRole,
    ExternalConnection,
    ExternalConnectionRegistry,
    InvalidClientRoleError,
)
from watcherobot.runtime.daemon.pairing.protocol import (
    DeviceSessionEnd,
    HardwareHello,
    PairingProtocolError,
    build_hardware_hello_ack,
    build_hardware_hello_nack,
    parse_device_session_end,
    parse_hardware_hello,
)
from watcherobot.runtime.daemon.pairing.session import PairingSessionError
from watcherobot.runtime.daemon.routing.raw import RawFrameRouter

HardwareHelloAuthorizer = Callable[
    [HardwareHello, str],
    Awaitable[None] | None,
]
DeviceDisconnectListener = Callable[[str], Awaitable[None] | None]
DeviceSessionEndListener = Callable[
    [DeviceSessionEnd, str],
    Awaitable[None] | None,
]


class ExternalWebSocketServer:
    """Accept external clients and route non-control frames unchanged."""

    CLOSE_ROLE_REQUIRED = 4401
    CLOSE_INVALID_ROLE = 4403
    CLOSE_ROLE_LOCKED = 4409
    CLOSE_PAIRING_SESSION_REQUIRED = 4410
    CLOSE_PAIRING_CREDENTIAL_INVALID = 4411
    CLOSE_DEVICE_SLOT_OCCUPIED = 4412
    CLOSE_PAIRING_PROTOCOL_MISMATCH = 4413

    def __init__(
        self,
        *,
        host: str,
        port: int,
        registry: ExternalConnectionRegistry | None = None,
        router: RawFrameRouter | None = None,
        hardware_hello_authorizer: HardwareHelloAuthorizer | None = None,
        device_disconnect_listener: DeviceDisconnectListener | None = None,
        device_session_end_listener: DeviceSessionEndListener | None = None,
        hello_timeout_seconds: float = 5.0,
    ) -> None:
        if hello_timeout_seconds <= 0:
            raise ValueError("hello_timeout_seconds must be positive")
        self._host = host
        self._requested_port = port
        self._bound_port: int | None = None
        self.registry = registry or ExternalConnectionRegistry()
        self.router = router or RawFrameRouter(self.registry)
        self._hardware_hello_authorizer = hardware_hello_authorizer
        self._device_disconnect_listener = device_disconnect_listener
        self._device_session_end_listener = device_session_end_listener
        self._hello_timeout_seconds = hello_timeout_seconds
        self._server: Server | None = None

    @property
    def url(self) -> str:
        if self._bound_port is None:
            raise RuntimeError("external WebSocket server is not running")
        return f"ws://{self._host}:{self._bound_port}"

    @property
    def bound_port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("external WebSocket server is not running")
        return self._bound_port

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await serve(
            self._handle_connection,
            self._host,
            self._requested_port,
            max_size=None,
        )
        sockets = self._server.sockets
        if not sockets:
            await self.stop()
            raise RuntimeError("external WebSocket server has no listening socket")
        self._bound_port = int(next(iter(sockets)).getsockname()[1])

    async def stop(self) -> None:
        await self.registry.close_all(
            code=1001,
            reason="Daemon external WebSocket server stopping",
        )
        server = self._server
        self._server = None
        self._bound_port = None
        if server is not None:
            server.close()
            await server.wait_closed()

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        connection = self.registry.add(websocket)
        try:
            try:
                try:
                    first_frame = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=self._hello_timeout_seconds,
                    )
                except TimeoutError:
                    await websocket.send(
                        json.dumps(
                            build_hardware_hello_nack(
                                code=401,
                                error="client_hello_required",
                            ),
                            separators=(",", ":"),
                        )
                    )
                    await websocket.close(
                        code=self.CLOSE_ROLE_REQUIRED,
                        reason="sys.client.hello timeout",
                    )
                    return
                if not await self._handle_hello(connection, first_frame):
                    return

                async for frame in websocket:
                    if self._is_control_message(frame, "sys.client.hello"):
                        await websocket.close(
                            code=self.CLOSE_ROLE_LOCKED,
                            reason="client role is already locked",
                        )
                        return
                    if self._is_control_message(frame, "sys.ping"):
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "sys.pong",
                                    "code": 0,
                                    "data": {},
                                },
                                separators=(",", ":"),
                            )
                        )
                        continue
                    if (
                        connection.role is ExternalClientRole.DEVICE
                        and self._is_control_message(
                            frame,
                            "sys.device.session.end",
                        )
                    ):
                        await self._handle_device_session_end(connection, frame)
                        return

                    await self.router.route_external(connection, frame)
            except ConnectionClosed:
                pass
        finally:
            removed = self.registry.remove(websocket)
            if (
                removed is not None
                and removed.role is ExternalClientRole.DEVICE
                and self._device_disconnect_listener is not None
            ):
                result = self._device_disconnect_listener(
                    self._peer_ip(websocket),
                )
                if inspect.isawaitable(result):
                    await result

    async def _handle_device_session_end(
        self,
        connection: ExternalConnection,
        frame: str | bytes,
    ) -> None:
        try:
            message = parse_device_session_end(frame)
        except PairingProtocolError:
            await connection.websocket.send(
                json.dumps(
                    {
                        "type": "sys.nack",
                        "code": 400,
                        "data": {
                            "type": "sys.device.session.end",
                            "error": "invalid_session_end",
                        },
                    },
                    separators=(",", ":"),
                )
            )
            return

        listener = self._device_session_end_listener
        if listener is None:
            await connection.websocket.send(
                json.dumps(
                    {
                        "type": "sys.nack",
                        "code": 500,
                        "data": {
                            "type": "sys.device.session.end",
                            "error": "internal_error",
                        },
                    },
                    separators=(",", ":"),
                )
            )
            return
        try:
            result = listener(
                message,
                self._peer_ip(connection.websocket),
            )
            if inspect.isawaitable(result):
                await result
        except PairingSessionError as exc:
            await connection.websocket.send(
                json.dumps(
                    {
                        "type": "sys.nack",
                        "code": 401,
                        "data": {
                            "type": "sys.device.session.end",
                            "error": exc.code,
                        },
                    },
                    separators=(",", ":"),
                )
            )
            return

        await connection.websocket.send(
            json.dumps(
                {
                    "type": "sys.ack",
                    "code": 0,
                    "data": {"type": "sys.device.session.end"},
                },
                separators=(",", ":"),
            )
        )
        await connection.websocket.close(
            code=1000,
            reason="device session ended",
        )

    async def _handle_hello(
        self,
        connection: ExternalConnection,
        frame: str | bytes,
    ) -> bool:
        payload = self._parse_control_payload(frame, "sys.client.hello")
        if payload is None:
            await connection.websocket.close(
                code=self.CLOSE_ROLE_REQUIRED,
                reason="sys.client.hello is required",
            )
            return False
        declared_role = str(payload.get("role", "")).strip().lower()
        hardware_ack: dict[str, object] | None = None
        if declared_role == ExternalClientRole.DEVICE.value:
            if self._hardware_hello_authorizer is None:
                await self._reject_hardware_hello(
                    connection,
                    code=401,
                    error="pairing_session_required",
                    close_code=self.CLOSE_PAIRING_SESSION_REQUIRED,
                )
                return False
            try:
                hello = parse_hardware_hello(frame)
            except PairingProtocolError:
                protocol_matches = (
                    payload.get("pairing_protocol") == "watcher-lan-pairing"
                    and payload.get("pairing_version") == "1.0"
                )
                if protocol_matches:
                    error = "pairing_credential_invalid"
                    code = 401
                    close_code = self.CLOSE_PAIRING_CREDENTIAL_INVALID
                else:
                    error = "pairing_protocol_mismatch"
                    code = 426
                    close_code = self.CLOSE_PAIRING_PROTOCOL_MISMATCH
                await self._reject_hardware_hello(
                    connection,
                    code=code,
                    error=error,
                    close_code=close_code,
                )
                return False
            try:
                result = self._hardware_hello_authorizer(
                    hello,
                    self._peer_ip(connection.websocket),
                )
                if inspect.isawaitable(result):
                    await result
            except PairingSessionError as exc:
                code, close_code = self._pairing_error_transport(exc.code)
                await self._reject_hardware_hello(
                    connection,
                    code=code,
                    error=exc.code,
                    close_code=close_code,
                )
                return False
            hardware_ack = build_hardware_hello_ack()
        try:
            role = self.registry.declare_role(
                connection,
                role=payload.get("role", ""),
            )
        except InvalidClientRoleError:
            await connection.websocket.close(
                code=self.CLOSE_INVALID_ROLE,
                reason="invalid client role",
            )
            return False
        except ClientRoleLockedError:
            await connection.websocket.close(
                code=self.CLOSE_ROLE_LOCKED,
                reason="client role is already locked",
            )
            return False

        if role is ExternalClientRole.DEVICE:
            assert hardware_ack is not None
            acknowledgement = hardware_ack
        else:
            acknowledgement = {
                "type": "sys.ack",
                "code": 0,
                "data": {
                    "type": "sys.client.hello",
                    "role": role.value,
                },
            }
        await connection.websocket.send(
            json.dumps(
                acknowledgement,
                separators=(",", ":"),
            )
        )
        return True

    async def _reject_hardware_hello(
        self,
        connection: ExternalConnection,
        *,
        code: int,
        error: str,
        close_code: int,
    ) -> None:
        await connection.websocket.send(
            json.dumps(
                build_hardware_hello_nack(code=code, error=error),
                separators=(",", ":"),
            )
        )
        await connection.websocket.close(
            code=close_code,
            reason=error,
        )

    @classmethod
    def _pairing_error_transport(cls, error: str) -> tuple[int, int]:
        if error == "pairing_credential_invalid":
            return 401, cls.CLOSE_PAIRING_CREDENTIAL_INVALID
        if error == "device_slot_occupied":
            return 409, cls.CLOSE_DEVICE_SLOT_OCCUPIED
        if error == "pairing_protocol_mismatch":
            return 426, cls.CLOSE_PAIRING_PROTOCOL_MISMATCH
        return 401, cls.CLOSE_PAIRING_SESSION_REQUIRED

    @staticmethod
    def _peer_ip(websocket: ServerConnection) -> str:
        remote = websocket.remote_address
        if isinstance(remote, tuple) and remote:
            return str(remote[0])
        return ""

    @classmethod
    def _is_control_message(cls, frame: str | bytes, expected_type: str) -> bool:
        return cls._parse_control_payload(frame, expected_type) is not None

    @staticmethod
    def _parse_control_payload(
        frame: str | bytes,
        expected_type: str,
    ) -> dict[str, Any] | None:
        if not isinstance(frame, str):
            return None
        try:
            message = json.loads(frame)
        except json.JSONDecodeError:
            return None
        if (
            not isinstance(message, dict)
            or message.get("type") != expected_type
            or not isinstance(message.get("data"), dict)
        ):
            return None
        return dict(message["data"])
