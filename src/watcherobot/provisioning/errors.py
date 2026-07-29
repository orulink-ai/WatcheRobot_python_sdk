"""Stable exceptions raised by Bluetooth provisioning."""

from __future__ import annotations

import asyncio

from watcherobot.errors import WatcheRobotError


class BluetoothProvisioningError(WatcheRobotError):
    """Base exception for Bluetooth provisioning failures."""


class BluetoothUnavailableError(BluetoothProvisioningError):
    """Bluetooth is disabled, unsupported, or otherwise unavailable."""


class BluetoothPermissionError(BluetoothProvisioningError):
    """The operating system denied Bluetooth access."""


class DeviceNotFoundError(BluetoothProvisioningError):
    """The requested Bluetooth device could not be found."""


class DeviceAmbiguityError(BluetoothProvisioningError):
    """More than one scanned device matched a requested identifier."""


class BluetoothConnectionTimeoutError(BluetoothProvisioningError):
    """Connecting to the Bluetooth device timed out."""


class PayloadTooLargeError(BluetoothProvisioningError):
    """A provisioning request exceeds the firmware payload limit."""

    def __init__(self, actual_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            "Provisioning request is too large "
            f"({actual_bytes} bytes; maximum {limit_bytes} bytes)"
        )
        self.actual_bytes = actual_bytes
        self.limit_bytes = limit_bytes


class ProvisioningProtocolError(BluetoothProvisioningError):
    """The device returned an invalid provisioning message."""


class ProvisioningRejectedError(BluetoothProvisioningError):
    """The firmware rejected a provisioning command."""

    def __init__(
        self,
        command_type: str,
        *,
        reason: str,
        code: int,
    ) -> None:
        super().__init__(
            f"Firmware rejected {command_type}: {reason} (code={code})"
        )
        self.command_type = command_type
        self.reason = reason
        self.code = code


class ProvisioningResponseTimeoutError(BluetoothProvisioningError):
    """No matching firmware response arrived before the deadline."""

    def __init__(self, command_type: str, timeout: float) -> None:
        super().__init__(
            f"Timed out waiting for {command_type} response "
            f"after {timeout:g} seconds"
        )
        self.command_type = command_type
        self.timeout = timeout


class ProvisioningCancelledError(
    asyncio.CancelledError,
    BluetoothProvisioningError,
):
    """A provisioning operation was cancelled by its caller."""

    def __init__(self) -> None:
        super().__init__("Bluetooth provisioning operation was cancelled")
