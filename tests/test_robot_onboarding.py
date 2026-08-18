from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from watcherobot.cli import (
    CliError,
    _scan_setup_devices,
    _select_setup_device,
    build_parser,
    main,
)
from watcherobot.provisioning import (
    BluetoothConnectionTimeoutError,
    BluetoothDevice,
    BluetoothPermissionError,
    BluetoothProvisioningError,
    BluetoothUnsupportedError,
    BluetoothUnavailableError,
    ProvisioningProtocolError,
    ProvisioningRejectedError,
    ProvisioningResponseTimeoutError,
    ProvisioningResult,
    ProtocolMessage,
    WifiConnectionFailedError,
    WifiStatus,
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

    async def scan_devices(
        self,
        *,
        timeout: float | None = None,
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
            on_status(
                WifiStatus(
                    state="connected",
                    ssid=ssid,
                    ip="192.168.1.9",
                )
            )
        return ProvisioningResult(
            device=device,
            ssid=ssid,
            state="connected",
            ack=ProtocolMessage(
                type="sys.ack",
                code=0,
                command_type="cfg.wifi.set",
                command_id="setup-1",
            ),
            wifi=WifiStatus(
                state="connected",
                ssid=ssid,
                ip="192.168.1.9",
            ),
        )


def _runtime_state() -> SimpleNamespace:
    return SimpleNamespace(control_url="http://runtime", pid=42)


def _enable_interactive_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "")


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
    assert "Robot is connecting to Wi-Fi" in output.out
    assert "Wi-Fi connected for" in output.out
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
    assert output.index("Settings > Wi-Fi") < output.index("Scanning")
    assert "up to 10 seconds" in output
    assert "Scan complete: 1 robot found" in output
    assert "Device ID: WR-A1B2-C3D4-E5F6-0708" in output
    assert "Bluetooth ID" not in output
    assert "WatcheRobot A1" not in output
    assert 'Open the "Python SDK" app' in output
    assert "Robot confirmed Wi-Fi connectivity" in output
    assert "top of the screen" in output
    assert "watcherobot robot pair <code>" in output
    assert "Robot connected successfully" in output


def test_robot_setup_shows_progress_during_a_slow_scan(
    monkeypatch,
    capsys,
) -> None:
    class SlowProvisioner(FakeProvisioner):
        async def scan_devices(
            self,
            *,
            timeout: float | None = None,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            assert timeout == 10.0
            assert name_filter is None
            await asyncio.sleep(0.01)
            return [self.device]

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        SlowProvisioner,
    )
    monkeypatch.setattr(
        "watcherobot.cli._SETUP_SCAN_PROGRESS_INTERVAL_SECONDS",
        0.001,
    )
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(
            AssertionError("explicit pairing code must not add a prompt")
        ),
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    monkeypatch.setattr("watcherobot.cli.pair_robot", lambda _code: 0)

    assert (
        main(
            [
                "robot",
                "setup",
                "--device",
                FakeProvisioner.device.device_id or "",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    scan_line = output[output.index("Scanning") : output.index("Scan complete")]
    assert "up to 10 seconds" in scan_line
    assert "." in scan_line


def test_robot_setup_uses_semantic_colors_when_forced(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    monkeypatch.setattr("watcherobot.cli.pair_robot", lambda _code: 0)

    assert (
        main(
            [
                "robot",
                "setup",
                "--device",
                FakeProvisioner.device.device_id or "",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "\x1b[34mScanning" in output
    assert "\x1b[32mScan complete" in output
    assert "\x1b[34mRobot is connecting to Wi-Fi" in output
    assert "\x1b[32mWi-Fi connected for" in output
    assert "\x1b[36mDevice ID:" in output


def test_no_color_overrides_forced_setup_colors(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    monkeypatch.setattr("watcherobot.cli.pair_robot", lambda _code: 0)

    assert (
        main(
            [
                "robot",
                "setup",
                "--device",
                FakeProvisioner.device.device_id or "",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "\x1b[" not in output


def test_robot_setup_colors_failure_and_recovery_differently(
    monkeypatch,
    capsys,
) -> None:
    class FailingProvisioner(FakeProvisioner):
        async def scan_devices(
            self,
            *,
            timeout: float | None = None,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            raise BluetoothUnavailableError("Bluetooth is unavailable")

    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert main(["robot", "setup"]) == 2

    error = capsys.readouterr().err
    assert "\x1b[31mBluetooth is unavailable" in error
    assert "\x1b[33m  1. Turn on Bluetooth" in error


def test_robot_setup_cancels_and_reaps_an_interrupted_scan(
    monkeypatch,
) -> None:
    class BlockingProvisioner(FakeProvisioner):
        cancelled = False

        async def scan_devices(
            self,
            *,
            timeout: float | None = None,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )

    async def scenario() -> None:
        provisioner = BlockingProvisioner()
        scan = asyncio.create_task(_scan_setup_devices(provisioner))
        await asyncio.sleep(0)
        scan.cancel()
        with pytest.raises(asyncio.CancelledError):
            await scan
        assert provisioner.cancelled

    asyncio.run(scenario())


def test_robot_setup_reports_wrong_wifi_password_without_manual_confirmation(
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
            on_status=None,
        ) -> ProvisioningResult:
            if on_status is not None:
                on_status(WifiStatus(state="connecting", ssid=ssid))
            raise WifiConnectionFailedError("auth_failed")

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")

    assert (
        main(
            [
                "robot",
                "setup",
                "--device",
                FakeProvisioner.device.device_id or "",
                "--ssid",
                "Office",
            ]
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "could not authenticate" in error
    assert "Check the Wi-Fi password" in error


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
        async def scan_devices(
            self,
            *,
            timeout: float | None = None,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            return []

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        EmptyProvisioner,
    )
    _enable_interactive_setup(monkeypatch)

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
        (
            BluetoothUnsupportedError("BLE central role is unavailable"),
            "does not support the required Bluetooth mode",
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
        async def scan_devices(
            self,
            *,
            timeout: float | None = None,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            raise error

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )
    _enable_interactive_setup(monkeypatch)

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
            on_status=None,
        ) -> ProvisioningResult:
            raise BluetoothConnectionTimeoutError("connection timed out")

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    _enable_interactive_setup(monkeypatch)

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
            on_status=None,
        ) -> ProvisioningResult:
            raise error

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    _enable_interactive_setup(monkeypatch)

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


def test_robot_setup_does_not_mask_an_unexpected_value_error(
    monkeypatch,
) -> None:
    class FailingProvisioner(FakeProvisioner):
        async def scan_devices(
            self,
            *,
            timeout: float | None = None,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            raise ValueError("internal setup bug")

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )

    with pytest.raises(ValueError, match="internal setup bug"):
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


def test_robot_setup_keeps_pairing_failure_in_the_guided_flow(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    _enable_interactive_setup(monkeypatch)

    def fail_pairing(_pairing_code: str) -> int:
        raise CliError(
            "Robot pairing 123456 timed out. Confirm that both devices use the "
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
    assert "Robot pairing <pairing-code> timed out" in output
    assert "123456" not in output
    assert '"Python SDK" app' in output
    assert "Settings > Wi-Fi shows Connected" in output
    assert "Wi-Fi name or password" in output
    assert '"error"' not in output


def test_robot_setup_non_interactive_failure_keeps_json_contract(
    monkeypatch,
    capsys,
) -> None:
    class FailingProvisioner(FakeProvisioner):
        async def scan_devices(
            self,
            *,
            timeout: float | None = None,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            raise BluetoothUnavailableError("Bluetooth is unavailable")

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FailingProvisioner,
    )
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: False,
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

    error = json.loads(capsys.readouterr().err)
    assert error == {"error": "Bluetooth is unavailable"}


def test_robot_setup_non_interactive_missing_value_keeps_json_contract(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: False,
    )

    assert (
        main(
            [
                "robot",
                "setup",
                "--device",
                FakeProvisioner.device.device_id or "",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    error = json.loads(capsys.readouterr().err)
    assert error == {
        "error": "Wi-Fi name is required in non-interactive use"
    }


def test_robot_setup_non_interactive_pairing_failure_keeps_safe_json(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: False,
    )
    monkeypatch.setattr(
        "watcherobot.cli.pair_robot",
        lambda _code: (_ for _ in ()).throw(
            CliError("Pairing code 123456 expired")
        ),
    )

    assert (
        main(
            [
                "robot",
                "setup",
                "--device",
                FakeProvisioner.device.device_id or "",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    error = json.loads(capsys.readouterr().err)
    assert error == {"error": "Pairing code <pairing-code> expired"}


def test_robot_setup_preserves_pairing_cancellation_exit_code(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    monkeypatch.setattr("watcherobot.cli.pair_robot", lambda _code: 130)
    _enable_interactive_setup(monkeypatch)

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
        == 130
    )

    error = capsys.readouterr().err
    assert "Robot setup cancelled." in error
    assert "123456" not in error


def test_robot_setup_eof_matches_ctrl_c_cancellation(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli._is_interactive_terminal",
        lambda: True,
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(EOFError),
    )

    assert main(["robot", "setup"]) == 130

    error = capsys.readouterr().err
    assert "Robot setup cancelled." in error
    assert '"error"' not in error


def test_robot_setup_reports_ambiguous_device_with_recovery(
    monkeypatch,
    capsys,
) -> None:
    class AmbiguousProvisioner(FakeProvisioner):
        async def scan_devices(
            self,
            *,
            timeout: float | None = None,
            name_filter: str | None = None,
        ) -> list[BluetoothDevice]:
            return [
                self.device,
                BluetoothDevice(
                    id="robot-2",
                    name="WatcheRobot A2",
                    rssi=-45,
                    is_watcher=True,
                    device_id=self.device.device_id,
                    _native=object(),
                ),
            ]

    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        AmbiguousProvisioner,
    )
    _enable_interactive_setup(monkeypatch)

    assert (
        main(
            [
                "robot",
                "setup",
                "--device",
                FakeProvisioner.device.device_id or "",
                "--ssid",
                "Office",
                "--pairing-code",
                "123456",
            ]
        )
        == 2
    )

    error = capsys.readouterr().err
    assert "More than one robot matched" in error
    assert "without --device" in error
    assert '"error"' not in error


def test_robot_setup_treats_nonzero_pair_result_as_pairing_failure(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "watcherobot.cli.BluetoothProvisioner",
        FakeProvisioner,
    )
    monkeypatch.setattr("watcherobot.cli.getpass", lambda _prompt: "secret")
    monkeypatch.setattr("watcherobot.cli.pair_robot", lambda _code: 1)
    _enable_interactive_setup(monkeypatch)

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
    assert "Robot pairing could not be completed" in error
    assert "Runtime pairing ended" in error


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
