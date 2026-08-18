"""Bleak implementation of the provisioning backend."""

from __future__ import annotations

import asyncio
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.exc import (
    BleakBluetoothNotAvailableError,
    BleakBluetoothNotAvailableReason,
    BleakDeviceNotFoundError,
)

from .backend import BleConnection, NotificationCallback
from .errors import (
    BluetoothConnectionTimeoutError,
    BluetoothPermissionError,
    BluetoothProvisioningError,
    BluetoothUnsupportedError,
    BluetoothUnavailableError,
    DeviceNotFoundError,
    ProvisioningProtocolError,
)
from .models import BluetoothDevice
from .protocol import (
    BLE_CHARACTERISTIC_UUID,
    BLE_DEVICE_NAME,
    BLE_SERVICE_UUID,
)

_MAX_CLEANUP_TIMEOUT = 2.0


class BleakConnection(BleConnection):
    def __init__(self, client: BleakClient, characteristic: Any) -> None:
        self._client = client
        self._characteristic = characteristic
        self.characteristic_uuid = str(characteristic.uuid).lower()

    async def start_notifications(
        self,
        callback: NotificationCallback,
    ) -> None:
        def handle_notification(_sender: Any, data: bytearray) -> None:
            callback(bytes(data))

        try:
            await self._client.start_notify(
                self._characteristic,
                handle_notification,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _map_platform_error(
                exc,
                operation="notification subscription",
            ) from exc

    async def stop_notifications(self) -> None:
        if self._client.is_connected:
            await self._client.stop_notify(self._characteristic)

    async def write(self, payload: bytes) -> None:
        try:
            await self._client.write_gatt_char(
                self._characteristic,
                payload,
                response=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _map_platform_error(exc, operation="write") from exc

    async def read(self) -> bytes:
        try:
            return bytes(
                await self._client.read_gatt_char(self._characteristic)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _map_platform_error(exc, operation="read") from exc

    async def disconnect(self) -> None:
        if self._client.is_connected:
            await self._client.disconnect()


class BleakBackend:
    """Cross-platform Windows/macOS backend powered by Bleak."""

    async def scan_devices(
        self,
        *,
        timeout: float,
        name_filter: str | None,
    ) -> list[BluetoothDevice]:
        try:
            discovered = await BleakScanner.discover(
                timeout=timeout,
                return_adv=True,
            )
        except Exception as exc:
            raise _map_platform_error(exc, operation="scan") from exc

        devices: list[BluetoothDevice] = []
        for native, advertisement in discovered.values():
            advertised_name = (
                getattr(advertisement, "local_name", None)
                or getattr(native, "name", None)
            )
            service_uuids = {
                str(item).lower()
                for item in getattr(advertisement, "service_uuids", ())
            }
            is_watcher = (
                advertised_name == BLE_DEVICE_NAME
                or BLE_SERVICE_UUID in service_uuids
            )
            if name_filter is not None and advertised_name != name_filter:
                continue
            devices.append(
                BluetoothDevice(
                    id=str(getattr(native, "address", "")),
                    name=advertised_name,
                    rssi=_advertisement_rssi(advertisement),
                    is_watcher=is_watcher,
                    device_id=_advertised_device_id(advertisement),
                    _native=native,
                )
            )
        return sorted(devices, key=lambda item: item.id)

    async def connect(
        self,
        device: BluetoothDevice,
        *,
        timeout: float,
    ) -> BleConnection:
        if device._native is None:
            raise DeviceNotFoundError(
                f"Bluetooth device {device.id!r} has no scan handle"
            )
        client = BleakClient(
            device._native,
            timeout=timeout,
            services=[BLE_SERVICE_UUID],
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=timeout)
        except asyncio.CancelledError:
            await _safe_disconnect(client, timeout=timeout)
            raise
        except asyncio.TimeoutError as exc:
            await _safe_disconnect(client, timeout=timeout)
            raise BluetoothConnectionTimeoutError(
                f"Timed out connecting to Bluetooth device {device.id!r}"
            ) from exc
        except Exception as exc:
            await _safe_disconnect(client, timeout=timeout)
            raise _map_platform_error(exc, operation="connect") from exc

        characteristic = client.services.get_characteristic(
            BLE_CHARACTERISTIC_UUID
        )
        if characteristic is None:
            await _safe_disconnect(client, timeout=timeout)
            raise ProvisioningProtocolError(
                "Device does not expose the required FF01 characteristic"
            )
        properties = {
            str(property_name).lower()
            for property_name in characteristic.properties
        }
        if (
            "write" not in properties
            or "read" not in properties
            or "notify" not in properties
        ):
            await _safe_disconnect(client, timeout=timeout)
            raise ProvisioningProtocolError(
                "FF01 does not provide read, write, and notify capabilities"
            )
        return BleakConnection(client, characteristic)


def _advertisement_rssi(advertisement: Any) -> int | None:
    value = getattr(advertisement, "rssi", None)
    return value if isinstance(value, int) else None


def _advertised_device_id(advertisement: Any) -> str | None:
    service_data = getattr(advertisement, "service_data", {})
    if not isinstance(service_data, dict):
        return None
    payload = next(
        (
            value
            for key, value in service_data.items()
            if str(key).lower() == BLE_SERVICE_UUID
        ),
        None,
    )
    if not isinstance(payload, (bytes, bytearray)) or len(payload) != 10:
        return None
    if payload[0] != 1:
        return None
    identity = payload[2:]
    pairs = [identity[index : index + 2].hex().upper() for index in range(0, 8, 2)]
    return f"WR-{'-'.join(pairs)}"


async def _safe_disconnect(
    client: BleakClient,
    *,
    timeout: float,
) -> None:
    cleanup_timeout = max(0.0, min(timeout, _MAX_CLEANUP_TIMEOUT))
    try:
        await asyncio.wait_for(
            client.disconnect(),
            timeout=cleanup_timeout,
        )
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def _map_platform_error(
    exc: Exception,
    *,
    operation: str,
) -> BluetoothProvisioningError:
    if isinstance(exc, BleakDeviceNotFoundError):
        return DeviceNotFoundError(
            f"Bluetooth device was not found during {operation}"
        )
    if isinstance(exc, BleakBluetoothNotAvailableError):
        denied_reasons = {
            BleakBluetoothNotAvailableReason.DENIED_BY_USER,
            BleakBluetoothNotAvailableReason.DENIED_BY_SYSTEM,
            BleakBluetoothNotAvailableReason.DENIED_BY_UNKNOWN,
        }
        if exc.reason in denied_reasons:
            return BluetoothPermissionError(
                f"Bluetooth permission was denied during {operation}"
            )
        if exc.reason == BleakBluetoothNotAvailableReason.NO_BLE_CENTRAL_ROLE:
            return BluetoothUnsupportedError(
                "Bluetooth Low Energy central role is not supported"
            )
        return BluetoothUnavailableError(
            f"Bluetooth is unavailable during {operation}"
        )
    details = str(exc).lower()
    if any(
        marker in details
        for marker in (
            "permission",
            "access denied",
            "not authorized",
            "unauthorized",
        )
    ):
        return BluetoothPermissionError(
            f"Bluetooth permission was denied during {operation}"
        )
    if any(
        marker in details
        for marker in (
            "not available",
            "powered off",
            "turned off",
            "no bluetooth",
            "adapter",
        )
    ):
        return BluetoothUnavailableError(
            f"Bluetooth is unavailable during {operation}"
        )
    if any(
        marker in details
        for marker in (
            "device not found",
            "not found",
            "unreachable",
        )
    ):
        return DeviceNotFoundError(
            f"Bluetooth device was not found during {operation}"
        )
    if operation == "scan":
        return BluetoothUnavailableError("Bluetooth scan failed")
    return BluetoothProvisioningError("Bluetooth connection failed")
