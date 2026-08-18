from __future__ import annotations

import argparse
import json

from watcherobot.cli import build_parser, main
from watcherobot.provisioning import (
    BluetoothDevice,
    ProvisioningCancelledError,
    ProvisioningResult,
    ProtocolMessage,
    WifiStatus,
)


class FakeProvisioner:
    device = BluetoothDevice(
        id="device-1",
        name="ESP_ROBOT",
        rssi=-40,
        is_watcher=True,
        _native=object(),
    )
    provision_calls: list[dict[str, object]] = []

    async def scan_devices(
        self,
        *,
        timeout: float = 10.0,
        name_filter: str | None = None,
    ) -> list[BluetoothDevice]:
        return [self.device]

    async def provision_wifi(
        self,
        device: BluetoothDevice,
        *,
        ssid: str,
        password: str,
        clear_existing: bool = False,
        on_status=None,
    ) -> ProvisioningResult:
        self.provision_calls.append(
            {
                "device": device,
                "ssid": ssid,
                "password": password,
                "clear_existing": clear_existing,
            }
        )
        if on_status is not None:
            on_status(WifiStatus(state="connecting", ssid=ssid))
            on_status(WifiStatus(state="connected", ssid=ssid))
        return ProvisioningResult(
            device=device,
            ssid=ssid,
            state="connected",
            ack=ProtocolMessage(
                type="sys.ack",
                code=0,
                command_type="cfg.wifi.set",
                command_id="python-wifi-set-1",
            ),
            wifi=WifiStatus(state="connected", ssid=ssid),
        )

    async def get_wifi_status(
        self,
        device: BluetoothDevice,
    ) -> WifiStatus:
        return WifiStatus(state="connected", ssid="Office", ip="192.168.1.9")

    async def clear_wifi(self, device: BluetoothDevice) -> WifiStatus:
        return WifiStatus(state="unconfigured")


def test_bluetooth_scan_prints_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )

    assert main(["bluetooth", "scan"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "devices": [
            {
                "id": "device-1",
                "name": "ESP_ROBOT",
                "rssi": -40,
                "is_watcher": True,
            }
        ]
    }


def test_bluetooth_provision_reads_password_interactively(
    monkeypatch,
    capsys,
) -> None:
    FakeProvisioner.provision_calls.clear()
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")

    assert (
        main(
            [
                "bluetooth",
                "provision",
                "--device",
                "device-1",
                "--ssid",
                "Office",
                "--clear-existing",
            ]
        )
        == 0
    )

    output = capsys.readouterr()
    assert "secret" not in output.out
    assert "secret" not in output.err
    assert json.loads(output.out)["state"] == "connected"
    assert FakeProvisioner.provision_calls == [
        {
            "device": FakeProvisioner.device,
            "ssid": "Office",
            "password": "secret",
            "clear_existing": True,
        }
    ]


def test_bluetooth_status_and_clear_use_device_id(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )

    assert main(["bluetooth", "status", "--device", "device-1"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "connected"

    assert main(["bluetooth", "clear", "--device", "device-1"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "unconfigured"


def test_bluetooth_command_rejects_unknown_device(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )

    assert main(["bluetooth", "status", "--device", "missing"]) == 2

    assert "not found" in json.loads(capsys.readouterr().err)["error"]


def test_bluetooth_command_rejects_ambiguous_device_id(
    monkeypatch,
    capsys,
) -> None:
    class AmbiguousProvisioner(FakeProvisioner):
        async def scan_devices(
            self,
            *,
            timeout: float = 10.0,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            return [
                self.device,
                BluetoothDevice(
                    id=self.device.id,
                    name=self.device.name,
                    rssi=-55,
                    is_watcher=True,
                ),
            ]

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        AmbiguousProvisioner,
    )

    assert main(["bluetooth", "status", "--device", "device-1"]) == 2
    assert "ambiguous" in json.loads(capsys.readouterr().err)["error"]


def test_bluetooth_provision_has_no_password_argument() -> None:
    parser = build_parser()
    command_subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    bluetooth_parser = command_subparsers.choices["bluetooth"]
    bluetooth_subparsers = next(
        action
        for action in bluetooth_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    provision_parser = bluetooth_subparsers.choices["provision"]

    assert all(
        action.dest != "password"
        for action in provision_parser._actions
    )


def test_bluetooth_ctrl_c_returns_cancelled_exit_code(
    monkeypatch,
    capsys,
) -> None:
    async def interrupted(_args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "watcherobot.cli._run_bluetooth_command",
        interrupted,
    )

    assert main(["bluetooth", "scan"]) == 130
    assert "cancelled" in json.loads(capsys.readouterr().err)["error"]


def test_bluetooth_stable_cancellation_returns_cancelled_exit_code(
    monkeypatch,
    capsys,
) -> None:
    async def cancelled(_args: argparse.Namespace) -> int:
        raise ProvisioningCancelledError

    monkeypatch.setattr(
        "watcherobot.cli._run_bluetooth_command",
        cancelled,
    )

    assert main(["bluetooth", "scan"]) == 130
    assert "cancelled" in json.loads(capsys.readouterr().err)["error"]
