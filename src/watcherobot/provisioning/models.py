"""Public, secret-free data models for Bluetooth provisioning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

WifiState = Literal[
    "connected",
    "connecting",
    "disconnected",
    "unconfigured",
]
ProvisioningState = Literal["credentials_saved"]


@dataclass(frozen=True)
class BluetoothDevice:
    """A scanned BLE device.

    ``id`` is platform-native. On macOS it is normally a CoreBluetooth UUID,
    not a hardware MAC address.
    """

    id: str
    name: str | None
    rssi: int | None
    is_watcher: bool
    device_id: str | None = None
    _native: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "rssi": self.rssi,
            "is_watcher": self.is_watcher,
        }
        if self.device_id is not None:
            result["device_id"] = self.device_id
        return result


@dataclass(frozen=True)
class ProtocolMessage:
    """A sanitized provisioning protocol message.

    Request bodies and other arbitrary fields are intentionally not retained,
    which prevents Wi-Fi passwords from entering reprs or result snapshots.
    """

    type: str
    code: int | None = None
    command_type: str | None = None
    command_id: str | None = None
    reason: str | None = None
    status: WifiState | None = None
    ssid: str | None = None
    ip: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"type": self.type}
        for key in (
            "code",
            "command_type",
            "command_id",
            "reason",
            "status",
            "ssid",
            "ip",
        ):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True)
class WifiStatus:
    state: WifiState
    ssid: str | None = None
    ip: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"state": self.state}
        if self.ssid is not None:
            result["ssid"] = self.ssid
        if self.ip is not None:
            result["ip"] = self.ip
        return result


@dataclass(frozen=True)
class ProvisioningResult:
    device: BluetoothDevice
    ssid: str
    state: ProvisioningState
    ack: ProtocolMessage

    def to_dict(self) -> dict[str, object]:
        return {
            "device": self.device.to_dict(),
            "ssid": self.ssid,
            "state": self.state,
            "ack": self.ack.to_dict(),
        }
