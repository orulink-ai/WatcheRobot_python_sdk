from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from watcherobot.cli import CliError, _select_setup_device, build_parser, main
from watcherobot.provisioning import (
    BluetoothConnectionTimeoutError,
    BluetoothDevice,
    BluetoothPermissionError,
    BluetoothProvisioningError,
    BluetoothUnavailableError,
    ProvisioningProtocolError,
    ProvisioningRejectedError,
    ProvisioningResponseTimeoutError,
    ProvisioningResult,
    ProtocolMessage,
)


class FakeProvisioner:
    device = BluetoothDevice(
        id="robot-1",
        name="WatcheRobot A1",
        rssi=-38,
        is_watcher=True,
        device_id="WR-A1B2-C3D4-E5F6-0708",
        _native=object(),
    )
    provision_calls: list[dict[str, object]] = []

    async def scan_devices(self) -> list[BluetoothDevice]:
        return [self.device]

    async def provision_wifi(
        self,
        device: BluetoothDevice,
        *,
        ssid: str,
        password: str,
        clear_existing: bool = False,
    ) -> ProvisioningResult:
        self.provision_calls.append(
            {
                "device": device,
                "ssid": ssid,
                "password": password,
                "clear_existing": clear_existing,
            }
        )
        return ProvisioningResult(
            device=device,
            ssid=ssid,
            state="credentials_saved",
            ack=ProtocolMessage(
                type="sys.ack",
                code=0,
                command_type="cfg.wifi.set",
                command_id="setup-1",
            ),
        )


def _runtime_state() -> SimpleNamespace:
    return SimpleNamespace(control_url="http://runtime", pid=42)


def test_robot_help_exposes_guided_setup_pair_and_status(capsys) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as captured:
        parser.parse_args(["robot", "--help"])

    assert captured.value.code == 0
    output = capsys.readouterr().out
    assert "setup" in output
    assert "pair" in output
    assert "status" in output


def test_robot_status_explains_how_to_connect_when_runtime_is_stopped(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr("watcherobot.cli._live_runtime_state", lambda: None)

    assert main(["robot", "status"]) == 1

    output = capsys.readouterr().out
    assert "Robot is not connected" in output
    assert "watcherobot robot setup" in output


def test_robot_pair_starts_runtime_and_waits_until_connected(
    monkeypatch,
    capsys,
) -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []
    device_states = iter(
        [
            {"device": {"state": "idle", "online": False}},
            {"device": {"state": "discovering", "online": False}},
            {"device": {"state": "connected", "online": True}},
        ]
    )
    monkeypatch.setattr(
        "watcherobot.cli.ensure_runtime",
        lambda: (_runtime_state(), False),
    )
    monkeypatch.setattr("watcherobot.cli.time.sleep", lambda _seconds: None)

    def request_json(
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        **_kwargs,
    ) -> dict[str, object]:
        requests.append((path, method, payload))
        if path == "/daemon/devices":
            return next(device_states)
        return {"device": {"state": "discovering", "online": False}}

    monkeypatch.setattr("watcherobot.cli._request_json", request_json)

    assert main(["robot", "pair", "123456"]) == 0

    assert (
        "/daemon/devices/pair",
        "POST",
        {"pairing_code": "123456", "target_mode": "python_sdk"},
    ) in requests
    output = capsys.readouterr().out
    assert "Robot connected successfully" in output
    assert "123456" not in output


def test_robot_pair_rejects_an_invalid_pairing_code(capsys) -> None:
    with pytest.raises(SystemExit) as captured:
        main(["robot", "pair", "12345"])

    assert captured.value.code == 2
    assert "six digits" in capsys.readouterr().err


def test_robot_setup_provisions_wifi_then_pairs_without_exposing_password(
    monkeypatch,
    capsys,
) -> None:
    FakeProvisioner.provision_calls.clear()
    requests: list[tuple[str, str, dict[str, object] | None]] = []
    device_states = iter(
        [
            {"device": {"state": "idle", "online": False}},
            {"device": {"state": "connected", "online": True}},
        ]
    )
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    monkeypatch.setattr(
        "watcherobot.cli.ensure_runtime",
        lambda: (_runtime_state(), False),
    )

    def request_json(
        base_url: str,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        **_kwargs,
    ) -> dict[str, object]:
        requests.append((path, method, payload))
        if path == "/daemon/devices":
            return next(device_states)
        return {"device": {"state": "discovering", "online": False}}

    monkeypatch.setattr("watcherobot.cli._request_json", request_json)

    assert (
        main(
            [
                "robot",
                "setup",
                "--device",
                "WR-A1B2-C3D4-E5F6-0708",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 0
    )

    assert FakeProvisioner.provision_calls == [
        {
            "device": FakeProvisioner.device,
            "ssid": "Office",
            "password": "secret",
            "clear_existing": False,
        }
    ]
    assert requests[-1][0] == "/daemon/devices"
    output = capsys.readouterr()
    assert "Device ID: WR-A1B2-C3D4-E5F6-0708" in output.out
    assert "Bluetooth ID" not in output.out
    assert "WatcheRobot A1" not in output.out
    assert "Wi-Fi credentials saved" in output.out
    assert "Robot connected successfully" in output.out
    assert "secret" not in output.out
    assert "secret" not in output.err


def test_robot_setup_without_arguments_guides_the_complete_interactive_flow(
    monkeypatch,
    capsys,
) -> None:
    FakeProvisioner.provision_calls.clear()
    answers = iter(["", "Office", "123456"])
    prompts: list[str] = []
    device_states = iter(
        [
            {"device": {"state": "idle", "online": False}},
            {"device": {"state": "connected", "online": True}},
        ]
    )
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )
    monkeypatch.setattr(
        "watcherobot.cli.getpass",
        lambda prompt: prompts.append(prompt) or "secret",
    )
    monkeypatch.setattr(
        "watcherobot.cli.ensure_runtime",
        lambda: (_runtime_state(), False),
    )

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    def request_json(
        _base_url: str,
        path: str,
        **_kwargs,
    ) -> dict[str, object]:
        if path == "/daemon/devices":
            return next(device_states)
        return {"device": {"state": "discovering", "online": False}}

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr("watcherobot.cli._request_json", request_json)

    assert main(["robot", "setup"]) == 0

    assert prompts == [
        "Press Enter after opening Settings > Wi-Fi on the robot: ",
        "Wi-Fi name: ",
        "Wi-Fi password: ",
        "Enter the 6-digit pairing code: ",
    ]
    output = capsys.readouterr().out
    assert "Turn on Bluetooth on this computer" in output
    assert output.index("Settings > Wi-Fi") < output.index(
        "Scanning for nearby WatcheRobot devices"
    )
    assert "Device ID: WR-A1B2-C3D4-E5F6-0708" in output
    assert "Bluetooth ID" not in output
    assert "WatcheRobot A1" not in output
    assert 'Open the "Python SDK" app' in output
    assert "top of the screen" in output
    assert "watcherobot robot pair <code>" in output
    assert "Robot connected successfully" in output


def test_robot_setup_uses_arrow_keys_to_choose_a_device_id(
    monkeypatch,
    capsys,
) -> None:
    devices = [
        BluetoothDevice(
            id="bluetooth-a",
            name="WatcheRobot Alpha",
            rssi=-30,
            is_watcher=True,
            device_id="WR-AAAA-BBBB-CCCC-DDDD",
            _native=object(),
        ),
        BluetoothDevice(
            id="bluetooth-b",
            name="WatcheRobot Beta",
            rssi=-40,
            is_watcher=True,
            device_id="WR-1111-2222-3333-4444",
            _native=object(),
        ),
    ]
    keys = iter(["down", "select"])
    monkeypatch.setattr(
        "watcherobot.cli._read_setup_menu_key",
        lambda: next(keys),
    )
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )

    selected = _select_setup_device(devices, requested_id=None)

    assert selected.id == "bluetooth-b"
    output = capsys.readouterr().out
    assert "Up/Down" in output
    assert "Device ID" in output
    assert "WR-AAAA-BBBB-CCCC-DDDD" in output
    assert "WR-1111-2222-3333-4444" in output
    assert "bluetooth-a" not in output
    assert "bluetooth-b" not in output
    assert "WatcheRobot Alpha" not in output
    assert "WatcheRobot Beta" not in output


def test_robot_setup_labels_missing_device_id_without_misrepresenting_bluetooth_id(
    monkeypatch,
    capsys,
) -> None:
    device = BluetoothDevice(
        id="legacy-bluetooth-id",
        name="ESP_ROBOT",
        rssi=-45,
        is_watcher=True,
        _native=object(),
    )

    selected = _select_setup_device([device], requested_id=None)

    assert selected is device
    output = capsys.readouterr().out
    assert "Device ID unavailable" in output
    assert "firmware update may be required" in output
    assert "Bluetooth ID: legacy-bluetooth-id" in output


def test_robot_setup_accepts_legacy_bluetooth_id_as_device_argument(
    capsys,
) -> None:
    device = BluetoothDevice(
        id="legacy-bluetooth-id",
        name="ESP_ROBOT",
        rssi=-45,
        is_watcher=True,
        _native=object(),
    )

    selected = _select_setup_device(
        [device],
        requested_id="LEGACY-BLUETOOTH-ID",
    )

    assert selected is device
    assert "Device ID unavailable" in capsys.readouterr().out


def test_robot_setup_matches_device_id_without_case_sensitivity() -> None:
    device = BluetoothDevice(
        id="bluetooth-id",
        name="ESP_ROBOT",
        rssi=-45,
        is_watcher=True,
        device_id="WR-A1B2-C3D4-E5F6-0708",
        _native=object(),
    )

    selected = _select_setup_device(
        [device],
        requested_id="wr-a1b2-c3d4-e5f6-0708",
    )

    assert selected is device


def test_bluetooth_device_preserves_legacy_native_positional_argument() -> None:
    native = object()

    device = BluetoothDevice("bluetooth-id", "ESP_ROBOT", -45, True, native)

    assert device._native is native
    assert device.device_id is None


def test_robot_setup_ctrl_c_at_guidance_exits_as_a_cancellation(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert main(["robot", "setup"]) == 130

    output = capsys.readouterr()
    assert "Robot setup cancelled." in output.err
    assert '"error"' not in output.err


def test_robot_setup_reports_when_no_robot_is_discoverable(
    monkeypatch,
    capsys,
) -> None:
    class EmptyProvisioner(FakeProvisioner):
        async def scan_devices(self) -> list[BluetoothDevice]:
            return []

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        EmptyProvisioner,
    )

    assert (
        main(
            [
                "robot",
                "setup",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "No WatcheRobot was found" in error
    assert "Settings > Wi-Fi" in error
    assert "watcherobot robot setup" in error
    assert '"error"' not in error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            BluetoothUnavailableError("Bluetooth is unavailable during scan"),
            "Turn on Bluetooth on this computer",
        ),
        (
            BluetoothPermissionError("Bluetooth permission was denied"),
            "Allow Bluetooth access",
        ),
    ],
)
def test_robot_setup_explains_how_to_recover_from_bluetooth_preflight_errors(
    error: Exception,
    expected: str,
    monkeypatch,
    capsys,
) -> None:
    class FailingProvisioner(FakeProvisioner):
        async def scan_devices(self) -> list[BluetoothDevice]:
            raise error

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )

    assert (
        main(
            [
                "robot",
                "setup",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    output = capsys.readouterr().err
    assert expected in output
    assert "watcherobot robot setup" in output
    assert '"error"' not in output


def test_robot_setup_explains_how_to_recover_from_connection_timeout(
    monkeypatch,
    capsys,
) -> None:
    class FailingProvisioner(FakeProvisioner):
        async def provision_wifi(
            self,
            device: BluetoothDevice,
            *,
            ssid: str,
            password: str,
            clear_existing: bool = False,
        ) -> ProvisioningResult:
            raise BluetoothConnectionTimeoutError("connection timed out")

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")

    assert (
        main(
            [
                "robot",
                "setup",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    output = capsys.readouterr().err
    assert "Bluetooth connection timed out" in output
    assert "Settings > Wi-Fi" in output
    assert "watcherobot robot setup" in output
    assert "secret" not in output


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ProvisioningRejectedError(
                "cfg.wifi.set",
                reason="invalid_wifi_payload",
                code=1,
            ),
            "Robot rejected the Wi-Fi settings",
        ),
        (
            ProvisioningResponseTimeoutError("cfg.wifi.set", 3.0),
            "Robot did not respond in time",
        ),
        (
            ProvisioningProtocolError("invalid response"),
            "Robot firmware returned an incompatible Bluetooth response",
        ),
        (
            BluetoothProvisioningError("unexpected failure"),
            "Robot setup could not be completed",
        ),
    ],
)
def test_robot_setup_gives_distinct_recovery_for_provisioning_failures(
    error: Exception,
    expected: str,
    monkeypatch,
    capsys,
) -> None:
    class FailingProvisioner(FakeProvisioner):
        async def provision_wifi(
            self,
            device: BluetoothDevice,
            *,
            ssid: str,
            password: str,
            clear_existing: bool = False,
        ) -> ProvisioningResult:
            raise error

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")

    assert (
        main(
            [
                "robot",
                "setup",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    output = capsys.readouterr().err
    assert expected in output
    assert "secret" not in output
    assert '"error"' not in output


def test_robot_setup_value_error_uses_guided_output_instead_of_json(
    monkeypatch,
    capsys,
) -> None:
    class FailingProvisioner(FakeProvisioner):
        async def scan_devices(self) -> list[BluetoothDevice]:
            raise ValueError("invalid setup value")

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )

    assert (
        main(
            [
                "robot",
                "setup",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    output = capsys.readouterr().err
    assert "Robot setup could not be completed" in output
    assert "invalid setup value" in output
    assert '"error"' not in output


def test_robot_setup_keeps_pairing_failure_in_the_guided_flow(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")

    def fail_pairing(_pairing_code: str) -> int:
        raise CliError(
            "Robot pairing timed out. Confirm that both devices use the "
            "same network and retry."
        )

    monkeypatch.setattr("watcherobot.cli.pair_robot", fail_pairing)

    assert (
        main(
            [
                "robot",
                "setup",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    output = capsys.readouterr().err
    assert "Robot pairing could not be completed" in output
    assert "Robot pairing timed out" in output
    assert '"Python SDK" app' in output
    assert '"error"' not in output


def test_app_run_without_robot_prints_an_actionable_setup_command(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    application = tmp_path / "application"
    application.mkdir()
    application.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "local.demo",
                "name": "Demo",
                "version": "0.1.0",
                "requires_watcherobot": ">=0.1.1a6,<0.2",
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    application.joinpath("app.py").write_text("", encoding="utf-8")
    states = iter(
        [
            {"device": {"state": "idle", "online": False}},
            {"application": {"state": "ended"}},
        ]
    )
    monkeypatch.setattr(
        "watcherobot.cli.ensure_runtime",
        lambda: (_runtime_state(), False),
    )

    def request_json(*_args, **_kwargs) -> dict[str, object]:
        return next(states, {"application": {"state": "ended"}})

    monkeypatch.setattr("watcherobot.cli._request_json", request_json)

    assert main(["app", "run", str(application)]) == 0

    output = capsys.readouterr().out
    assert "No robot is connected" in output
    assert "watcherobot robot setup" in output
    assert "watcherobot robot pair <code>" in output
