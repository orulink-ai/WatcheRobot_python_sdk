"""Replaceable backend contracts for Bluetooth provisioning."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .models import BluetoothDevice

NotificationCallback = Callable[[bytes], None]


class BleConnection(Protocol):
    characteristic_uuid: str

    async def start_notifications(
        self,
        callback: NotificationCallback,
    ) -> None:
        """Subscribe to FF01 notifications."""

    async def stop_notifications(self) -> None:
        """Stop FF01 notifications."""

    async def write(self, payload: bytes) -> None:
        """Write FF01 with an ATT response."""

    async def read(self) -> bytes:
        """Read the firmware's cached FF01 response."""

    async def disconnect(self) -> None:
        """Disconnect from the peripheral."""


class BluetoothBackend(Protocol):
    async def scan_devices(
        self,
        *,
        timeout: float,
        name_filter: str | None,
    ) -> list[BluetoothDevice]:
        """Scan while preserving backend-native device objects."""

    async def connect(
        self,
        device: BluetoothDevice,
        *,
        timeout: float,
    ) -> BleConnection:
        """Connect and validate the required GATT characteristic."""
