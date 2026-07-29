"""External Runtime connection identity and online state."""

from .device_state import (
    DeviceConnectionState,
    DeviceConnectionStateRegistry,
)
from .registry import (
    ClientRoleLockedError,
    ConnectionRegistryError,
    ExternalClientRole,
    ExternalConnection,
    ExternalConnectionRegistry,
    InvalidClientRoleError,
)
from .identity import (
    DeviceIdentity,
    normalize_device_id,
    normalize_mac,
    resolve_device_identity,
    resolve_discovery_identity,
)
from .websocket_server import ExternalWebSocketServer

__all__ = [
    "ClientRoleLockedError",
    "ConnectionRegistryError",
    "DeviceConnectionState",
    "DeviceConnectionStateRegistry",
    "DeviceIdentity",
    "ExternalClientRole",
    "ExternalConnection",
    "ExternalConnectionRegistry",
    "ExternalWebSocketServer",
    "InvalidClientRoleError",
    "normalize_device_id",
    "normalize_mac",
    "resolve_device_identity",
    "resolve_discovery_identity",
]
