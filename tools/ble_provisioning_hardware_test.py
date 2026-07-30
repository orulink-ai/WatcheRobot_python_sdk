#!/usr/bin/env python3
"""Interactive BLE provisioning smoke test for physical WatcheRobot devices."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from getpass import getpass
from typing import Sequence

from watcherobot.provisioning import (
    BluetoothDevice,
    BluetoothProvisioner,
    BluetoothProvisioningError,
    ProvisioningCancelledError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan and exercise WatcheRobot BLE Wi-Fi provisioning.",
    )
    parser.add_argument(
        "--scan-timeout",
        type=float,
        default=10.0,
        help="BLE scan timeout in seconds (default: 10).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="List nearby BLE devices.")

    for command, help_text in (
        ("status", "Read the saved Wi-Fi status."),
        ("clear", "Clear saved Wi-Fi credentials."),
    ):
        child = commands.add_parser(command, help=help_text)
        _add_device_selector(child)

    provision = commands.add_parser("provision", help="Save Wi-Fi credentials.")
    _add_device_selector(provision)
    provision.add_argument("--ssid", required=True, help="Wi-Fi SSID.")
    provision.add_argument(
        "--clear-existing",
        action="store_true",
        help="Clear saved credentials before provisioning.",
    )
    return parser


def _add_device_selector(parser: argparse.ArgumentParser) -> None:
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--device", help="Exact platform BLE device ID.")
    selector.add_argument(
        "--id-prefix",
        help="Unique device-ID prefix, for example 80:B5 on Windows.",
    )


def select_device(
    devices: Sequence[BluetoothDevice],
    *,
    device_id: str | None,
    id_prefix: str | None,
) -> BluetoothDevice:
    """Resolve exactly one recognized WatcheRobot without silently guessing."""
    if device_id is not None:
        matches = [device for device in devices if device.id == device_id]
    elif id_prefix is not None:
        prefix = id_prefix.casefold()
        matches = [
            device for device in devices if device.id.casefold().startswith(prefix)
        ]
    else:
        raise ValueError("a device selector is required")

    if not matches:
        raise ValueError("no scanned device matched the selector")
    if len(matches) > 1:
        raise ValueError(
            f"device selector is ambiguous: {len(matches)} scanned devices matched"
        )
    if not matches[0].is_watcher:
        raise ValueError("the matched device is not a recognized ESP_ROBOT")
    return matches[0]


def _emit(payload: object, *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
    )


async def _run(args: argparse.Namespace) -> int:
    provisioner = BluetoothProvisioner(scan_timeout=args.scan_timeout)
    devices = await provisioner.scan_devices()
    if args.command == "scan":
        _emit({"devices": [device.to_dict() for device in devices]})
        return 0

    device = select_device(
        devices,
        device_id=args.device,
        id_prefix=args.id_prefix,
    )
    if args.command == "status":
        result = await provisioner.get_wifi_status(device)
    elif args.command == "clear":
        result = await provisioner.clear_wifi(device)
    else:
        password = getpass("Wi-Fi password (leave empty for an open network): ")
        try:
            result = await provisioner.provision_wifi(
                device,
                ssid=args.ssid,
                password=password,
                clear_existing=args.clear_existing,
            )
        finally:
            password = ""

    _emit(result.to_dict())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (KeyboardInterrupt, ProvisioningCancelledError):
        _emit({"error": "operation cancelled"}, error=True)
        return 130
    except (BluetoothProvisioningError, ValueError) as exc:
        _emit({"error": str(exc)}, error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
