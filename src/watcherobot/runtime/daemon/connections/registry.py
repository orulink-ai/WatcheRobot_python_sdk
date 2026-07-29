"""Minimal external WebSocket connection identity and online state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from websockets.asyncio.server import ServerConnection

from .device_state import DeviceConnectionStateRegistry


class ExternalClientRole(str, Enum):
    UNKNOWN = "unknown"
    DESKTOP = "desktop"
    DEVICE = "hardware"


class ConnectionRegistryError(RuntimeError):
    """Base error for external connection identity operations."""


class InvalidClientRoleError(ConnectionRegistryError):
    """Raised when a client declares a role Daemon doesn't accept."""


class ClientRoleLockedError(ConnectionRegistryError):
    """Raised when a connection attempts to declare its role twice."""


@dataclass
class ExternalConnection:
    websocket: ServerConnection
    role: ExternalClientRole = ExternalClientRole.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)


class ExternalConnectionRegistry:
    """Track only connection role and online state."""

    def __init__(self) -> None:
        self._connections: dict[ServerConnection, ExternalConnection] = {}
        self.device_states = DeviceConnectionStateRegistry()

    def add(self, websocket: ServerConnection) -> ExternalConnection:
        connection = ExternalConnection(websocket=websocket)
        self._connections[websocket] = connection
        return connection

    def remove(
        self,
        websocket: ServerConnection,
    ) -> ExternalConnection | None:
        connection = self._connections.pop(websocket, None)
        if (
            connection is not None
            and connection.role is ExternalClientRole.DEVICE
        ):
            self.device_states.disconnect(websocket)
        return connection

    def declare_role(
        self,
        connection: ExternalConnection,
        *,
        role: str,
    ) -> ExternalClientRole:
        if connection.role is not ExternalClientRole.UNKNOWN:
            raise ClientRoleLockedError(
                f"client role is already locked as {connection.role.value}"
            )
        try:
            declared_role = ExternalClientRole(str(role or "").strip().lower())
        except ValueError as exc:
            raise InvalidClientRoleError(
                f"unsupported client role: {role}"
            ) from exc
        if declared_role is ExternalClientRole.UNKNOWN:
            raise InvalidClientRoleError(
                "client role must be desktop or hardware"
            )

        connection.role = declared_role
        if declared_role is ExternalClientRole.DEVICE:
            state = self.device_states.connect(connection.websocket)
            connection.metadata = state.to_dict()
        else:
            connection.metadata = {}
        return declared_role

    def connections_for(
        self,
        role: ExternalClientRole,
    ) -> list[ExternalConnection]:
        return [
            connection
            for connection in self._connections.values()
            if connection.role is role
        ]

    def online_count(self, role: ExternalClientRole) -> int:
        return len(self.connections_for(role))

    async def close_role(
        self,
        role: ExternalClientRole,
        *,
        code: int,
        reason: str,
    ) -> int:
        connections = list(self.connections_for(role))
        for connection in connections:
            await connection.websocket.close(code=code, reason=reason)
        return len(connections)

    async def send_to_role(
        self,
        role: ExternalClientRole,
        frame: str | bytes,
        *,
        exclude: ExternalConnection | None = None,
    ) -> int:
        sent = 0
        for connection in list(self.connections_for(role)):
            if connection is exclude:
                continue
            await connection.websocket.send(frame)
            sent += 1
        return sent

    async def close_all(self, *, code: int, reason: str) -> None:
        for connection in list(self._connections.values()):
            await connection.websocket.close(code=code, reason=reason)
