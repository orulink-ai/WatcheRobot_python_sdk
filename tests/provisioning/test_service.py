from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from watcherobot.provisioning import (
    BluetoothDevice,
    BluetoothProvisioner,
    ProvisioningCancelledError,
    ProvisioningRejectedError,
    ProvisioningResponseTimeoutError,
)
from watcherobot.provisioning.backend import BleConnection
from watcherobot.provisioning.protocol import BLE_CHARACTERISTIC_UUID


class FakeConnection(BleConnection):
    def __init__(
        self,
        *,
        fragmented_notifications: bool = False,
        reject_wifi_set: bool = False,
        block_write: bool = False,
        block_read: bool = False,
        no_response: bool = False,
        wrong_ack_first: bool = False,
        duplicate_ack: bool = False,
        read_returns_echo: bool = False,
        stale_status_on_start: bool = False,
        reject_clear_after_status: bool = False,
        delayed_reject_clear_after_status: bool = False,
        empty_read: bool = False,
        legacy_status_on_start: bool = True,
        block_stop_notifications: bool = False,
        block_disconnect: bool = False,
    ) -> None:
        self.fragmented_notifications = fragmented_notifications
        self.reject_wifi_set = reject_wifi_set
        self.block_write = block_write
        self.block_read = block_read
        self.no_response = no_response
        self.wrong_ack_first = wrong_ack_first
        self.duplicate_ack = duplicate_ack
        self.read_returns_echo = read_returns_echo
        self.stale_status_on_start = stale_status_on_start
        self.reject_clear_after_status = reject_clear_after_status
        self.delayed_reject_clear_after_status = (
            delayed_reject_clear_after_status
        )
        self.empty_read = empty_read
        self.legacy_status_on_start = legacy_status_on_start
        self.block_stop_notifications = block_stop_notifications
        self.block_disconnect = block_disconnect
        self.characteristic_uuid = BLE_CHARACTERISTIC_UUID
        self.writes: list[dict[str, Any]] = []
        self.cached = b""
        self.callback: Callable[[bytes], None] | None = None
        self.notifications_started = False
        self.notifications_stopped = False
        self.stop_notifications_started = False
        self.disconnected = False
        self.disconnect_started = False
        self.write_started = asyncio.Event()

    async def start_notifications(
        self,
        callback: Callable[[bytes], None],
    ) -> None:
        self.notifications_started = True
        self.callback = callback
        if self.legacy_status_on_start:
            callback(b"WIFI_UNCONFIGURED\n")
        if self.stale_status_on_start:
            callback(
                b'{"type":"evt.wifi.status","code":0,'
                b'"data":{"status":"connected","ssid":"Stale"}}'
            )

    async def stop_notifications(self) -> None:
        self.stop_notifications_started = True
        if self.block_stop_notifications:
            await asyncio.Event().wait()
        self.notifications_stopped = True

    async def write(self, payload: bytes) -> None:
        self.write_started.set()
        if self.block_write:
            await asyncio.Event().wait()
        request = json.loads(payload.decode("utf-8"))
        self.writes.append(request)
        if self.no_response:
            self.cached = b""
            return
        request_type = request["type"]
        command_id = request["data"].get("command_id")
        if (
            self.reject_wifi_set
            and request_type == "cfg.wifi.set"
        ) or (
            (
                self.reject_clear_after_status
                or self.delayed_reject_clear_after_status
            )
            and request_type == "cfg.wifi.clear"
        ):
            response = {
                "type": "sys.nack",
                "code": 400,
                "data": {
                    "type": request_type,
                    "command_id": command_id,
                    "reason": "invalid_wifi_payload",
                },
            }
        else:
            response = {
                "type": "sys.ack",
                "code": 0,
                "data": {
                    "type": request_type,
                    "command_id": command_id,
                },
            }
        encoded = json.dumps(response, separators=(",", ":")).encode("utf-8")
        self.cached = encoded
        if (
            self.delayed_reject_clear_after_status
            and request_type == "cfg.wifi.clear"
            and self.callback is not None
        ):
            callback = self.callback
            self.cached = b""
            callback(
                b'{"type":"evt.wifi.status","code":0,'
                b'"data":{"status":"connected","ssid":"Stale"}}'
            )

            async def send_delayed_rejection() -> None:
                await asyncio.sleep(0.1)
                callback(encoded)

            asyncio.create_task(send_delayed_rejection())
            return
        if (
            self.reject_clear_after_status
            and request_type == "cfg.wifi.clear"
            and self.callback is not None
        ):
            self.callback(
                b'{"type":"evt.wifi.status","code":0,'
                b'"data":{"status":"disconnected"}}'
            )
            return
        if self.wrong_ack_first and self.callback is not None:
            wrong = {
                **response,
                "data": {
                    **response["data"],
                    "command_id": "another-command",
                },
            }
            self.callback(
                json.dumps(wrong, separators=(",", ":")).encode("utf-8")
            )
        if self.fragmented_notifications and self.callback is not None:
            midpoint = len(encoded) // 2
            self.callback(encoded[:midpoint])
            self.callback(encoded[midpoint:])
        if self.duplicate_ack and self.callback is not None:
            self.callback(encoded)
            self.callback(encoded)
        if self.read_returns_echo:
            if self.callback is not None:
                self.callback(encoded)
            self.cached = payload
        if request_type in {"cfg.wifi.get", "cfg.wifi.clear"}:
            if self.callback is not None:
                self.callback(encoded)
            state = (
                "unconfigured"
                if request_type == "cfg.wifi.clear"
                else "connected"
            )
            status = json.dumps(
                {
                    "type": "evt.wifi.status",
                    "code": 0,
                    "data": {
                        "status": state,
                        **(
                            {"ssid": "Office", "ip": "192.168.1.9"}
                            if state == "connected"
                            else {}
                        ),
                    },
                },
                separators=(",", ":"),
            ).encode("utf-8")
            if self.callback is not None:
                self.callback(status)
            self.cached = status

    async def read(self) -> bytes:
        if self.block_read:
            await asyncio.Event().wait()
        if self.empty_read:
            return b""
        return self.cached

    async def disconnect(self) -> None:
        self.disconnect_started = True
        if self.block_disconnect:
            await asyncio.Event().wait()
        self.disconnected = True


class FakeBackend:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.native = object()
        self.device = BluetoothDevice(
            id="device-1",
            name="ESP_ROBOT",
            rssi=-45,
            is_watcher=True,
            _native=self.native,
        )
        self.scan_calls: list[tuple[float, str | None]] = []
        self.connect_calls: list[tuple[BluetoothDevice, float]] = []

    async def scan_devices(
        self,
        *,
        timeout: float,
        name_filter: str | None,
    ) -> list[BluetoothDevice]:
        self.scan_calls.append((timeout, name_filter))
        return [self.device]

    async def connect(
        self,
        device: BluetoothDevice,
        *,
        timeout: float,
    ) -> BleConnection:
        self.connect_calls.append((device, timeout))
        return self.connection


def test_scan_returns_watcher_devices() -> None:
    async def scenario() -> None:
        backend = FakeBackend(FakeConnection())
        provisioner = BluetoothProvisioner(backend=backend)

        devices = await provisioner.scan_devices()

        assert devices == [backend.device]
        assert backend.scan_calls == [(10.0, None)]

    asyncio.run(scenario())


def test_provision_wifi_defaults_to_set_without_clear_and_cleans_up() -> None:
    async def scenario() -> None:
        connection = FakeConnection(fragmented_notifications=True)
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        result = await provisioner.provision_wifi(
            backend.device,
            ssid="Office",
            password="secret",
        )

        assert result.state == "credentials_saved"
        assert result.ssid == "Office"
        assert result.ack.command_type == "cfg.wifi.set"
        assert [item["type"] for item in connection.writes] == [
            "cfg.wifi.set"
        ]
        assert "secret" not in repr(result)
        assert connection.notifications_started
        assert connection.notifications_stopped
        assert connection.disconnected

    asyncio.run(scenario())


def test_provision_wifi_can_clear_existing_credentials_first() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        await provisioner.provision_wifi(
            backend.device,
            ssid="Office",
            password="secret",
            clear_existing=True,
        )

        assert [item["type"] for item in connection.writes] == [
            "cfg.wifi.clear",
            "cfg.wifi.set",
        ]

    asyncio.run(scenario())


def test_status_and_clear_return_firmware_wifi_state() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        status = await provisioner.get_wifi_status(backend.device)
        cleared = await provisioner.clear_wifi(backend.device)

        assert status.state == "connected"
        assert status.ssid == "Office"
        assert status.ip == "192.168.1.9"
        assert cleared.state == "unconfigured"

    asyncio.run(scenario())


def test_nack_is_raised_as_stable_rejection_and_connection_is_closed() -> None:
    async def scenario() -> None:
        connection = FakeConnection(reject_wifi_set=True)
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        with pytest.raises(ProvisioningRejectedError) as captured:
            await provisioner.provision_wifi(
                backend.device,
                ssid="Office",
                password="secret",
            )

        assert captured.value.reason == "invalid_wifi_payload"
        assert connection.disconnected

    asyncio.run(scenario())


def test_matching_nack_wins_over_unrelated_status_notification() -> None:
    async def scenario() -> None:
        connection = FakeConnection(reject_clear_after_status=True)
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        with pytest.raises(ProvisioningRejectedError) as captured:
            await provisioner.clear_wifi(backend.device)

        assert captured.value.command_type == "cfg.wifi.clear"
        assert connection.disconnected

    asyncio.run(scenario())


def test_delayed_matching_nack_wins_over_unrelated_status_notification() -> None:
    async def scenario() -> None:
        connection = FakeConnection(delayed_reject_clear_after_status=True)
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        with pytest.raises(ProvisioningRejectedError) as captured:
            await provisioner.clear_wifi(backend.device)

        assert captured.value.command_type == "cfg.wifi.clear"
        assert connection.disconnected

    asyncio.run(scenario())


def test_ack_matching_ignores_wrong_command_and_duplicate_messages() -> None:
    async def scenario() -> None:
        connection = FakeConnection(
            wrong_ack_first=True,
            duplicate_ack=True,
        )
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        result = await provisioner.provision_wifi(
            backend.device,
            ssid="Office",
            password="secret",
        )

        assert result.ack.command_id != "another-command"
        assert result.state == "credentials_saved"

    asyncio.run(scenario())


def test_request_echo_is_ignored_and_does_not_leak_password() -> None:
    async def scenario() -> None:
        connection = FakeConnection(read_returns_echo=True)
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        result = await provisioner.provision_wifi(
            backend.device,
            ssid="Office",
            password="do-not-leak",
        )

        assert "do-not-leak" not in repr(result)
        assert result.state == "credentials_saved"

    asyncio.run(scenario())


def test_notify_is_used_when_cached_read_is_empty() -> None:
    async def scenario() -> None:
        connection = FakeConnection(
            fragmented_notifications=True,
            empty_read=True,
        )
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        result = await provisioner.provision_wifi(
            backend.device,
            ssid="Office",
            password="secret",
        )

        assert result.state == "credentials_saved"

    asyncio.run(scenario())


def test_stale_status_notification_does_not_satisfy_new_request() -> None:
    async def scenario() -> None:
        connection = FakeConnection(stale_status_on_start=True)
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)

        status = await provisioner.get_wifi_status(backend.device)

        assert status.ssid == "Office"

    asyncio.run(scenario())


@pytest.mark.parametrize("block_read", [False, True])
def test_response_timeout_cleans_up_connection(block_read: bool) -> None:
    async def scenario() -> None:
        connection = FakeConnection(
            no_response=not block_read,
            block_read=block_read,
        )
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(
            backend=backend,
            response_timeout=0.01,
        )

        with pytest.raises(ProvisioningResponseTimeoutError):
            await provisioner.provision_wifi(
                backend.device,
                ssid="Office",
                password="secret",
            )

        assert connection.notifications_stopped
        assert connection.disconnected

    asyncio.run(scenario())


def test_task_cancellation_still_disconnects_the_device() -> None:
    async def scenario() -> None:
        connection = FakeConnection(block_write=True)
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(backend=backend)
        task = asyncio.create_task(
            provisioner.provision_wifi(
                backend.device,
                ssid="Office",
                password="secret",
            )
        )
        await connection.write_started.wait()

        task.cancel()
        with pytest.raises(ProvisioningCancelledError):
            await task

        assert connection.notifications_stopped
        assert connection.disconnected

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("ssid", "password", "field"),
    [
        ("", "secret", "SSID"),
        ("s" * 32, "secret", "SSID"),
        ("网" * 11, "secret", "SSID"),
        ("Office", "p" * 64, "password"),
        ("Office", "密" * 22, "password"),
        ("Office\0Hidden", "secret", "SSID"),
        ("Office", "secret\0Hidden", "password"),
    ],
)
def test_credentials_are_validated_by_utf8_length(
    ssid: str,
    password: str,
    field: str,
) -> None:
    async def scenario() -> None:
        backend = FakeBackend(FakeConnection())
        provisioner = BluetoothProvisioner(backend=backend)

        with pytest.raises(ValueError, match=field):
            await provisioner.provision_wifi(
                backend.device,
                ssid=ssid,
                password=password,
            )

        assert backend.connect_calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("block_stop_notifications", "block_disconnect"),
    [
        (True, False),
        (False, True),
    ],
)
def test_cleanup_steps_are_bounded_and_disconnect_is_always_attempted(
    block_stop_notifications: bool,
    block_disconnect: bool,
) -> None:
    async def scenario() -> None:
        connection = FakeConnection(
            block_stop_notifications=block_stop_notifications,
            block_disconnect=block_disconnect,
        )
        backend = FakeBackend(connection)
        provisioner = BluetoothProvisioner(
            backend=backend,
            cleanup_timeout=0.01,
        )

        result = await asyncio.wait_for(
            provisioner.provision_wifi(
                backend.device,
                ssid="Office",
                password="secret",
            ),
            timeout=0.2,
        )

        assert result.state == "credentials_saved"
        assert connection.stop_notifications_started
        assert connection.disconnect_started

    asyncio.run(scenario())
