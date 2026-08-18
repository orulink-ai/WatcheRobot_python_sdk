from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from bleak.exc import (
    BleakBluetoothNotAvailableError,
    BleakBluetoothNotAvailableReason,
)

from watcherobot.provisioning import (
    BluetoothConnectionTimeoutError,
    BluetoothDevice,
    BluetoothPermissionError,
    BluetoothUnsupportedError,
    BluetoothUnavailableError,
    DeviceNotFoundError,
    ProvisioningProtocolError,
)
from watcherobot.provisioning.bleak_backend import (
    BleakBackend,
    _advertised_device_id,
)
from watcherobot.provisioning.protocol import (
    BLE_CHARACTERISTIC_UUID,
    BLE_SERVICE_UUID,
)


class NativeDevice:
    address = "platform-device-id"
    name = "Operating System Name"


class Advertisement:
    local_name = "ESP_ROBOT"
    rssi = -42
    service_uuids = [BLE_SERVICE_UUID]
    service_data = {
        BLE_SERVICE_UUID: bytes.fromhex("0100A1B2C3D4E5F60708"),
    }


class Characteristic:
    uuid = BLE_CHARACTERISTIC_UUID
    properties = ["read", "write", "notify"]


class Services:
    def __init__(self, characteristic: Characteristic | None) -> None:
        self.characteristic = characteristic

    def get_characteristic(self, uuid: str) -> Characteristic | None:
        assert uuid == BLE_CHARACTERISTIC_UUID
        return self.characteristic


class FakeBleakClient:
    instances: list[FakeBleakClient] = []
    characteristic: Characteristic | None = Characteristic()

    def __init__(
        self,
        native: object,
        *,
        timeout: float,
        services: list[str],
    ) -> None:
        self.native = native
        self.timeout = timeout
        self.requested_services = services
        self.services = Services(self.characteristic)
        self.is_connected = False
        self.notify_callback: Any = None
        self.write_args: tuple[object, bytes, bool] | None = None
        self.stopped = False
        self.disconnected = False
        self.instances.append(self)

    async def connect(self) -> None:
        self.is_connected = True

    async def start_notify(
        self,
        characteristic: object,
        callback: Any,
    ) -> None:
        self.notify_callback = callback

    async def stop_notify(self, characteristic: object) -> None:
        self.stopped = True

    async def write_gatt_char(
        self,
        characteristic: object,
        payload: bytes,
        *,
        response: bool,
    ) -> None:
        self.write_args = (characteristic, payload, response)

    async def read_gatt_char(
        self,
        characteristic: object,
    ) -> bytearray:
        return bytearray(b'{"type":"cached"}')

    async def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False


def test_bleak_backend_preserves_native_device_and_uses_response_write(
    monkeypatch,
) -> None:
    async def discover(**kwargs: object) -> dict[str, tuple[object, object]]:
        assert kwargs == {"timeout": 1.5, "return_adv": True}
        return {"platform-device-id": (NativeDevice(), Advertisement())}

    async def scenario() -> None:
        FakeBleakClient.instances.clear()
        FakeBleakClient.characteristic = Characteristic()
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakScanner.discover",
            discover,
        )
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakClient",
            FakeBleakClient,
        )
        backend = BleakBackend()

        devices = await backend.scan_devices(
            timeout=1.5,
            name_filter=None,
        )
        assert devices[0].id == "platform-device-id"
        assert devices[0].device_id == "WR-A1B2-C3D4-E5F6-0708"
        assert devices[0].is_watcher
        assert devices[0]._native is not None

        connection = await backend.connect(devices[0], timeout=2.0)
        await connection.start_notifications(lambda _data: None)
        await connection.write(b"request")
        assert await connection.read() == b'{"type":"cached"}'
        await connection.stop_notifications()
        await connection.disconnect()

        client = FakeBleakClient.instances[-1]
        assert client.native is devices[0]._native
        assert client.requested_services == [BLE_SERVICE_UUID]
        assert client.write_args is not None
        assert client.write_args[2] is True
        assert client.stopped
        assert client.disconnected

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "service_data",
    [
        None,
        {},
        {BLE_SERVICE_UUID: b"\x01\x00"},
        {BLE_SERVICE_UUID: bytes.fromhex("0200A1B2C3D4E5F60708")},
        {BLE_SERVICE_UUID: "not-bytes"},
    ],
)
def test_bleak_backend_rejects_missing_or_unsupported_device_identity(
    service_data: object,
) -> None:
    advertisement = SimpleNamespace(service_data=service_data)

    assert _advertised_device_id(advertisement) is None


def test_bleak_backend_accepts_bytearray_device_identity_and_uuid_case() -> None:
    advertisement = SimpleNamespace(
        service_data={
            BLE_SERVICE_UUID.upper(): bytearray.fromhex(
                "0100A1B2C3D4E5F60708"
            )
        }
    )

    assert (
        _advertised_device_id(advertisement)
        == "WR-A1B2-C3D4-E5F6-0708"
    )


def test_bleak_backend_rejects_characteristic_without_response_write(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        FakeBleakClient.instances.clear()
        FakeBleakClient.characteristic = Characteristic()
        FakeBleakClient.characteristic.properties = [
            "read",
            "write-without-response",
            "notify",
        ]
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakClient",
            FakeBleakClient,
        )
        backend = BleakBackend()
        device = (
            await _scan_one_with_backend(monkeypatch, backend)
        )

        with pytest.raises(ProvisioningProtocolError):
            await backend.connect(device, timeout=2.0)

        assert FakeBleakClient.instances[-1].disconnected

    asyncio.run(scenario())


def test_bleak_backend_maps_permission_denial_without_platform_details(
    monkeypatch,
) -> None:
    async def denied(**_kwargs: object) -> object:
        raise RuntimeError("Access denied by operating system")

    async def scenario() -> None:
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakScanner.discover",
            denied,
        )

        with pytest.raises(BluetoothPermissionError) as captured:
            await BleakBackend().scan_devices(
                timeout=1.0,
                name_filter=None,
            )

        assert "Access denied" not in str(captured.value)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "reason",
    [
        BleakBluetoothNotAvailableReason.POWERED_OFF,
        BleakBluetoothNotAvailableReason.NO_BLUETOOTH,
    ],
)
def test_bleak_backend_maps_disabled_or_missing_bluetooth_adapter(
    reason: BleakBluetoothNotAvailableReason,
    monkeypatch,
) -> None:
    async def unavailable(**_kwargs: object) -> object:
        raise BleakBluetoothNotAvailableError("platform details", reason)

    async def scenario() -> None:
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakScanner.discover",
            unavailable,
        )

        with pytest.raises(BluetoothUnavailableError) as captured:
            await BleakBackend().scan_devices(
                timeout=1.0,
                name_filter=None,
            )

        assert "platform details" not in str(captured.value)

    asyncio.run(scenario())


def test_bleak_backend_maps_missing_central_role_as_unsupported(
    monkeypatch,
) -> None:
    async def unsupported(**_kwargs: object) -> object:
        raise BleakBluetoothNotAvailableError(
            "platform details",
            BleakBluetoothNotAvailableReason.NO_BLE_CENTRAL_ROLE,
        )

    async def scenario() -> None:
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakScanner.discover",
            unsupported,
        )

        with pytest.raises(BluetoothUnsupportedError) as captured:
            await BleakBackend().scan_devices(
                timeout=1.0,
                name_filter=None,
            )

        assert "platform details" not in str(captured.value)

    asyncio.run(scenario())


def test_bleak_backend_disconnects_when_connection_is_cancelled(
    monkeypatch,
) -> None:
    class BlockingBleakClient(FakeBleakClient):
        async def connect(self) -> None:
            await asyncio.Event().wait()

    async def scenario() -> None:
        BlockingBleakClient.instances.clear()
        BlockingBleakClient.characteristic = Characteristic()
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakClient",
            BlockingBleakClient,
        )
        backend = BleakBackend()
        device = await _scan_one_with_backend(monkeypatch, backend)
        task = asyncio.create_task(
            backend.connect(device, timeout=2.0)
        )
        await asyncio.sleep(0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert BlockingBleakClient.instances[-1].disconnected

    asyncio.run(scenario())


def test_bleak_backend_disconnects_after_connection_timeout(
    monkeypatch,
) -> None:
    class BlockingBleakClient(FakeBleakClient):
        async def connect(self) -> None:
            await asyncio.Event().wait()

    async def scenario() -> None:
        BlockingBleakClient.instances.clear()
        BlockingBleakClient.characteristic = Characteristic()
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakClient",
            BlockingBleakClient,
        )
        backend = BleakBackend()
        device = await _scan_one_with_backend(monkeypatch, backend)

        with pytest.raises(BluetoothConnectionTimeoutError):
            await backend.connect(device, timeout=0.01)

        assert BlockingBleakClient.instances[-1].disconnected

    asyncio.run(scenario())


def test_bleak_backend_bounds_disconnect_after_connection_timeout(
    monkeypatch,
) -> None:
    class BlockingCleanupBleakClient(FakeBleakClient):
        disconnect_started = False

        async def connect(self) -> None:
            await asyncio.Event().wait()

        async def disconnect(self) -> None:
            self.disconnect_started = True
            await asyncio.Event().wait()

    async def scenario() -> None:
        BlockingCleanupBleakClient.instances.clear()
        BlockingCleanupBleakClient.characteristic = Characteristic()
        monkeypatch.setattr(
            "watcherobot.provisioning.bleak_backend.BleakClient",
            BlockingCleanupBleakClient,
        )
        backend = BleakBackend()
        device = await _scan_one_with_backend(monkeypatch, backend)

        with pytest.raises(BluetoothConnectionTimeoutError):
            await asyncio.wait_for(
                backend.connect(device, timeout=0.01),
                timeout=0.2,
            )

        assert BlockingCleanupBleakClient.instances[-1].disconnect_started

    asyncio.run(scenario())


def test_bleak_backend_requires_native_scan_handle() -> None:
    async def scenario() -> None:
        device = BluetoothDevice(
            id="manual-id",
            name="ESP_ROBOT",
            rssi=None,
            is_watcher=True,
        )

        with pytest.raises(DeviceNotFoundError):
            await BleakBackend().connect(device, timeout=1.0)

    asyncio.run(scenario())


async def _scan_one_with_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend: BleakBackend,
) -> Any:
    async def discover(**_kwargs: object) -> dict[str, tuple[object, object]]:
        return {"platform-device-id": (NativeDevice(), Advertisement())}

    monkeypatch.setattr(
        "watcherobot.provisioning.bleak_backend.BleakScanner.discover",
        discover,
    )
    return (
        await backend.scan_devices(timeout=1.0, name_filter=None)
    )[0]
