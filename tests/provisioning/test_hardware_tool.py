from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.ble_provisioning_hardware_test import build_parser, select_device
from watcherobot.provisioning import BluetoothDevice


def _device(device_id: str, *, is_watcher: bool = True) -> BluetoothDevice:
    return BluetoothDevice(
        id=device_id,
        name="ESP_ROBOT" if is_watcher else "Other",
        rssi=-50,
        is_watcher=is_watcher,
    )


def test_select_device_accepts_case_insensitive_unique_prefix() -> None:
    selected = select_device(
        [_device("80:B5:AA:00:00:01"), _device("12:34:56:78:90:AB")],
        device_id=None,
        id_prefix="80:b5",
    )

    assert selected.id == "80:B5:AA:00:00:01"


def test_select_device_rejects_ambiguous_prefix() -> None:
    devices = [
        _device("80:B5:AA:00:00:01"),
        _device("80:B5:AA:00:00:02"),
    ]

    with pytest.raises(ValueError, match="ambiguous"):
        select_device(devices, device_id=None, id_prefix="80:B5")


def test_select_device_rejects_unrecognized_device() -> None:
    with pytest.raises(ValueError, match="not a recognized"):
        select_device(
            [_device("80:B5:AA:00:00:01", is_watcher=False)],
            device_id="80:B5:AA:00:00:01",
            id_prefix=None,
        )


def test_provision_command_has_no_password_argument() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "provision",
                "--device",
                "80:B5:AA:00:00:01",
                "--ssid",
                "test",
                "--password",
                "must-not-be-accepted",
            ]
        )


def test_tool_can_run_directly_from_checkout() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(repository_root / "tools" / "ble_provisioning_hardware_test.py"),
            "--help",
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "provision" in completed.stdout
