"""Command-line entrypoint for the SDK-owned Runtime and Applications."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from getpass import getpass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from watcherobot import __version__
from watcherobot.application.project import (
    ApplicationProjectDefaults,
    ApplicationProjectInitError,
    ApplicationProjectInitResult,
    default_application_project_metadata,
    init_application_project,
)
from watcherobot.distribution.cli import (
    add_distribution_commands,
    is_distribution_command,
    run_command as run_distribution_command,
)
from watcherobot.distribution.install import (
    ApplicationInstallError,
    list_installed_applications,
)
from watcherobot.provisioning import (
    BluetoothConnectionTimeoutError,
    BluetoothDevice,
    BluetoothPermissionError,
    BluetoothProvisioner,
    BluetoothProvisioningError,
    BluetoothUnsupportedError,
    BluetoothUnavailableError,
    DeviceAmbiguityError,
    DeviceNotFoundError,
    ProvisioningCancelledError,
    ProvisioningProtocolError,
    ProvisioningRejectedError,
    ProvisioningResponseTimeoutError,
)
from watcherobot.runtime.daemon.application.manifest import ApplicationManifest
from watcherobot.runtime.daemon.instance import (
    RuntimeProcessState,
    RuntimeStateStore,
    default_runtime_state_root,
)


APPLICATION_START_TIMEOUT_SECONDS = 90.0
ROBOT_PAIR_TIMEOUT_SECONDS = 25.0
_SETUP_SCAN_PROGRESS_INTERVAL_SECONDS = 1.0
_PAIRING_CODE = re.compile(r"^[0-9]{6}$")
_PAIRING_CODE_IN_TEXT = re.compile(r"(?<![0-9])[0-9]{6}(?![0-9])")


class CliError(RuntimeError):
    pass


class RobotSetupError(CliError):
    """An expected input or interaction failure in guided robot setup."""


class RobotPairingError(CliError):
    """An expected Runtime pairing failure in guided robot setup."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watcherobot",
        description="WatcheRobot SDK command-line tools.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    daemon = commands.add_parser(
        "daemon",
        help="Start, inspect, or stop the SDK-owned Daemon",
    )
    daemon_commands = daemon.add_subparsers(
        dest="daemon_command",
        required=True,
    )
    daemon_commands.add_parser("start")
    daemon_commands.add_parser("status")
    daemon_commands.add_parser("stop")

    app = commands.add_parser(
        "app",
        help="Develop, run, publish, and inspect Applications",
        description=(
            "Application developer workflow. Distribution commands do not "
            "start the Daemon; run/start/stop are Daemon-managed."
        ),
        epilog=(
            "Typical workflow:\n"
            "  watcherobot app init hello_robot\n"
            "  cd hello_robot\n"
            "  watcherobot app run\n"
            "  watcherobot app login\n"
            "  watcherobot app publish .\\my_app\n"
            "  watcherobot app submit .\\my_app\n"
            "  watcherobot app marketplace\n\n"
            "For manual use, omit --jsonl. Desktop automation uses --jsonl.\n"
            "Installation, inventory, and removal are SDK distribution commands. "
            "Daemon selection remains a management action."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    app_commands = app.add_subparsers(
        dest="app_command",
        required=True,
        title="Application commands",
        metavar="COMMAND",
    )
    init = app_commands.add_parser(
        "init",
        help="Create a runnable Application project",
        description=(
            "Create a runnable Application project that plays a Hello World "
            "behavior. Metadata defaults from the project directory and can "
            "be overridden for publishing. When DIRECTORY is omitted, an "
            "interactive terminal prompts for it."
        ),
    )
    init.add_argument(
        "directory",
        type=Path,
        nargs="?",
        help="New project directory; prompted when omitted",
    )
    init.add_argument("--id", dest="app_id", help="Unique Application ID")
    init.add_argument("--name", help="Application display name")
    init.add_argument("--author", help="Developer or organization name")
    init.add_argument("--description", help="Short marketplace description")
    init.add_argument(
        "--platform",
        dest="supported_host_platforms",
        action="append",
        choices=("windows", "macos"),
        help="Supported host platform; repeat for Windows and macOS",
    )
    run = app_commands.add_parser(
        "run",
        help="Run a source directory through the SDK Daemon",
    )
    run.add_argument(
        "application",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Application source directory (default: current directory)",
    )
    run_installed = app_commands.add_parser(
        "run-installed",
        help="Run one installed Application from an isolated SDK App Store",
        description=(
            "Run one SDK-installed Application with a temporary Daemon bound "
            "to the specified App Store. This command does not use or modify "
            "the Desktop Daemon."
        ),
    )
    run_installed.add_argument(
        "--store-root",
        type=Path,
        required=True,
        help="SDK App Store root that contains the installed Application",
    )
    run_installed.add_argument(
        "--app-id",
        required=True,
        help="Installed Application ID to run",
    )
    add_distribution_commands(app_commands)
    app_commands.add_parser(
        "start",
        help="Start the Application currently selected by the Daemon",
    )
    app_commands.add_parser(
        "stop",
        help="Stop the currently running Application",
    )

    bluetooth = commands.add_parser(
        "bluetooth",
        help="Provision robot Wi-Fi over Bluetooth",
    )
    bluetooth_commands = bluetooth.add_subparsers(
        dest="bluetooth_command",
        required=True,
    )
    bluetooth_commands.add_parser("scan")
    provision = bluetooth_commands.add_parser("provision")
    provision.add_argument("--device", required=True)
    provision.add_argument("--ssid", required=True)
    provision.add_argument("--clear-existing", action="store_true")
    status = bluetooth_commands.add_parser("status")
    status.add_argument("--device", required=True)
    clear = bluetooth_commands.add_parser("clear")
    clear.add_argument("--device", required=True)

    robot = commands.add_parser(
        "robot",
        help="Set up, pair, and inspect your WatcheRobot",
        description=(
            "User-friendly robot onboarding. Setup provisions Wi-Fi over "
            "Bluetooth and then pairs the robot with the SDK Runtime."
        ),
    )
    robot_commands = robot.add_subparsers(
        dest="robot_command",
        required=True,
    )
    setup = robot_commands.add_parser(
        "setup",
        help="Guide Wi-Fi provisioning and Runtime pairing",
    )
    setup.add_argument(
        "--device",
        help="Robot Device ID (Bluetooth ID for legacy firmware)",
    )
    setup.add_argument("--ssid", help="Wi-Fi network name")
    setup.add_argument(
        "--pairing-code",
        type=_parse_pairing_code,
        help="Six-digit code shown by the robot",
    )
    setup.add_argument("--clear-existing", action="store_true")
    pair = robot_commands.add_parser(
        "pair",
        help="Pair an already networked robot with the SDK Runtime",
    )
    pair.add_argument(
        "pairing_code",
        type=_parse_pairing_code,
        help="Six-digit code shown by the robot",
    )
    robot_commands.add_parser(
        "status",
        help="Show whether a robot is connected",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "daemon":
            if args.daemon_command == "start":
                state, reused = ensure_runtime()
                _print_json(
                    {
                        "running": True,
                        "reused": reused,
                        "pid": state.pid,
                        "control_url": state.control_url,
                    }
                )
                return 0
            if args.daemon_command == "status":
                status = runtime_status()
                _print_json(status)
                return 0 if status["running"] else 1
            if args.daemon_command == "stop":
                stop_runtime()
                _print_json({"running": False})
                return 0
        if args.command == "app" and args.app_command == "run":
            return run_application(args.application)
        if args.command == "app" and args.app_command == "run-installed":
            return run_installed_application(
                store_root=args.store_root,
                application_id=args.app_id,
            )
        if args.command == "app" and args.app_command == "init":
            return _run_application_init(args)
        if is_distribution_command(args):
            return run_distribution_command(args)
        if args.command == "app":
            state, _reused = ensure_runtime()
            if args.app_command == "start":
                result = _request_json(
                    state.control_url,
                    "/daemon/application/start",
                    method="POST",
                    timeout=APPLICATION_START_TIMEOUT_SECONDS,
                )
                _print_application_runtime_result(
                    "Application started",
                    result,
                )
                return 0
            if args.app_command == "stop":
                result = _request_json(
                    state.control_url,
                    "/daemon/application/stop",
                    method="POST",
                )
                _print_application_runtime_result(
                    "Application stopped",
                    result,
                )
                return 0
        if args.command == "bluetooth":
            try:
                return asyncio.run(_run_bluetooth_command(args))
            except KeyboardInterrupt:
                return _print_bluetooth_cancelled()
            except ProvisioningCancelledError:
                return _print_bluetooth_cancelled()
            except ValueError as exc:
                raise CliError(str(exc)) from exc
        if args.command == "robot":
            if args.robot_command == "status":
                return robot_status()
            if args.robot_command == "pair":
                return pair_robot(args.pairing_code)
            if args.robot_command == "setup":
                try:
                    return asyncio.run(_run_robot_setup(args))
                except KeyboardInterrupt:
                    return _print_robot_setup_cancelled()
                except ProvisioningCancelledError:
                    return _print_robot_setup_cancelled()
                except BluetoothProvisioningError as exc:
                    if not _is_interactive_terminal():
                        raise
                    return _print_robot_setup_failure(exc)
                except RobotPairingError as exc:
                    if not _is_interactive_terminal():
                        raise
                    return _print_robot_pairing_failure(exc)
                except RobotSetupError as exc:
                    if not _is_interactive_terminal():
                        raise
                    return _print_robot_setup_input_failure(exc)
    except (
        ApplicationProjectInitError,
        BluetoothProvisioningError,
        CliError,
    ) as exc:
        print(
            json.dumps({"error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    raise CliError("unsupported command")


def _run_application_init(args: argparse.Namespace) -> int:
    directory = _application_init_directory(args.directory)
    values = _application_init_metadata(args, directory)
    result = init_application_project(
        directory,
        app_id=values["app_id"],
        name=values["name"],
        author=values["author"],
        description=values["description"],
        supported_host_platforms=args.supported_host_platforms
        or ["windows", "macos"],
    )
    _print_application_init_result(result)
    return 0


def _application_init_directory(directory: Path | None) -> Path:
    if directory is not None:
        return directory
    if not _is_interactive_terminal():
        raise CliError("Application project directory is required")
    try:
        supplied = input("Project directory [hello_robot]: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise CliError("Application initialization cancelled") from exc
    return Path(supplied or "hello_robot")


def _application_init_metadata(
    args: argparse.Namespace,
    directory: Path,
) -> dict[str, str]:
    defaults = default_application_project_metadata(directory)
    return {
        "app_id": _value_or_default(args.app_id, defaults, "app_id"),
        "name": _value_or_default(args.name, defaults, "name"),
        "author": _value_or_default(args.author, defaults, "author"),
        "description": _value_or_default(
            args.description,
            defaults,
            "description",
        ),
    }


def _value_or_default(
    value: object,
    defaults: ApplicationProjectDefaults,
    field: str,
) -> str:
    if _has_text(value):
        return str(value).strip()
    return str(getattr(defaults, field))


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty()


def _parse_pairing_code(value: str) -> str:
    pairing_code = str(value).strip()
    if _PAIRING_CODE.fullmatch(pairing_code) is None:
        raise argparse.ArgumentTypeError("pairing code must contain six digits")
    return pairing_code


def _print_application_init_result(
    result: ApplicationProjectInitResult,
) -> None:
    print("Application project created")
    print()
    fields = (
        ("Directory", str(result.directory)),
        ("ID", result.app_id),
        ("Name", result.name),
        ("Version", result.version),
        ("SDK", result.requires_watcherobot),
    )
    label_width = max(len(label) + 1 for label, _value in fields)
    for label, value in fields:
        print(f"{label + ':':<{label_width}}  {value}")
    print()
    print("Next:")
    print("  watcherobot robot setup  # first robot only")
    print(f'  cd "{result.directory}"')
    print("  watcherobot app run")


def _print_bluetooth_cancelled() -> int:
    print(
        json.dumps(
            {"error": "Bluetooth operation cancelled"},
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return 130


def _print_robot_setup_cancelled() -> int:
    print("Robot setup cancelled.", file=sys.stderr)
    return 130


def _print_robot_setup_failure(exc: BluetoothProvisioningError) -> int:
    lines: tuple[str, ...]
    if isinstance(exc, BluetoothUnsupportedError):
        lines = (
            "This computer does not support the required Bluetooth mode.",
            "  1. Use a Bluetooth Low Energy adapter with central support.",
            "  2. Confirm the operating system supports BLE scanning.",
            "  3. Run watcherobot robot setup again.",
        )
    elif isinstance(exc, BluetoothUnavailableError):
        lines = (
            "Bluetooth is unavailable on this computer.",
            "  1. Turn on Bluetooth on this computer.",
            "  2. Confirm that a Bluetooth adapter is available.",
            "  3. Run watcherobot robot setup again.",
        )
    elif isinstance(exc, BluetoothPermissionError):
        lines = (
            "Bluetooth access was denied.",
            "  1. Open this computer's system privacy settings.",
            "  2. Allow Bluetooth access for this terminal or Python.",
            "  3. Run watcherobot robot setup again.",
        )
    elif isinstance(exc, DeviceNotFoundError):
        lines = (
            "No WatcheRobot was found.",
            "  1. Keep the robot on Settings > Wi-Fi.",
            "  2. Keep the robot near this computer.",
            "  3. Run watcherobot robot setup again.",
            "Already on Wi-Fi? Open the robot's \"Python SDK\" app and run "
            "watcherobot robot pair <code> instead.",
        )
    elif isinstance(exc, DeviceAmbiguityError):
        lines = (
            "More than one robot matched that identifier.",
            "  1. Run watcherobot robot setup without --device.",
            "  2. Select the intended robot with Up/Down.",
            "  3. Confirm its Device ID on the robot screen.",
        )
    elif isinstance(exc, BluetoothConnectionTimeoutError):
        lines = (
            "Bluetooth connection timed out.",
            "  1. Keep the robot on Settings > Wi-Fi and nearby.",
            "  2. Close other apps that may be connected to the robot.",
            "  3. Run watcherobot robot setup again.",
        )
    elif isinstance(exc, ProvisioningRejectedError):
        lines = (
            "Robot rejected the Wi-Fi settings.",
            "  1. Keep the robot on Settings > Wi-Fi and nearby.",
            "  2. Check the Wi-Fi name and password.",
            "  3. Run watcherobot robot setup again.",
        )
    elif isinstance(exc, ProvisioningResponseTimeoutError):
        lines = (
            "Robot did not respond in time.",
            "  1. Keep the robot on Settings > Wi-Fi and nearby.",
            "  2. Close other apps that may be connected to the robot.",
            "  3. Run watcherobot robot setup again.",
        )
    elif isinstance(exc, ProvisioningProtocolError):
        lines = (
            "Robot firmware returned an incompatible Bluetooth response.",
            "  1. Update the robot firmware and WatcheRobot SDK.",
            "  2. Keep the robot on Settings > Wi-Fi and retry setup.",
            "  3. If it persists, report the firmware and SDK versions.",
        )
    else:
        lines = (
            "Robot setup could not be completed.",
            "  1. Keep the robot on Settings > Wi-Fi and nearby.",
            "  2. Run watcherobot robot setup again.",
        )
    print("\n".join(lines), file=sys.stderr)
    return 2


def _print_robot_setup_input_failure(exc: RobotSetupError) -> int:
    print("Robot setup could not be completed.", file=sys.stderr)
    print(str(exc), file=sys.stderr)
    return 2


def _print_robot_pairing_failure(exc: RobotPairingError) -> int:
    print("Robot pairing could not be completed.", file=sys.stderr)
    print(str(exc), file=sys.stderr)
    print(
        "  1. Confirm Settings > Wi-Fi shows Connected on the robot.",
        file=sys.stderr,
    )
    print(
        "  2. If it shows Offline, disconnect/forget the network and rerun "
        "setup; the Wi-Fi name or password may be incorrect.",
        file=sys.stderr,
    )
    print('  3. Keep the robot\'s "Python SDK" app open.', file=sys.stderr)
    print("  4. Enter the latest 6-digit code and retry.", file=sys.stderr)
    return 2


async def _run_robot_setup(args: argparse.Namespace) -> int:
    _prepare_robot_for_setup(
        wait_for_confirmation=(
            args.device is None and _is_interactive_terminal()
        )
    )
    provisioner = BluetoothProvisioner()
    devices = [
        device
        for device in await _scan_setup_devices(provisioner)
        if device.is_watcher
    ]
    robot_count = len(devices)
    robot_label = "robot" if robot_count == 1 else "robots"
    print(f"Scan complete: {robot_count} {robot_label} found.")
    device = _select_setup_device(devices, requested_id=args.device)
    ssid = _setup_text_value(
        args.ssid,
        prompt="Wi-Fi name: ",
        field_name="Wi-Fi name",
    )
    password = getpass("Wi-Fi password: ")
    try:
        await provisioner.provision_wifi(
            device,
            ssid=ssid,
            password=password,
            clear_existing=bool(args.clear_existing),
        )
    finally:
        del password

    print(
        "Wi-Fi credentials stored for "
        f"{_setup_device_identity(device)}; connection not verified."
    )
    _confirm_robot_wifi_connected()
    print()
    print("Next, complete pairing on the robot:")
    print("  1. Return to the robot launcher.")
    print('  2. Open the "Python SDK" app.')
    print("  3. Read the 6-digit pairing code at the top of the screen.")
    pairing_code = args.pairing_code
    if pairing_code is None:
        pairing_code = _setup_text_value(
            None,
            prompt="Enter the 6-digit pairing code: ",
            field_name="pairing code",
        )
        try:
            pairing_code = _parse_pairing_code(pairing_code)
        except argparse.ArgumentTypeError as exc:
            raise RobotSetupError(str(exc)) from exc
    try:
        result = pair_robot(pairing_code)
    except CliError as exc:
        raise RobotPairingError(
            _redact_pairing_codes(str(exc))
        ) from exc
    if result == 130:
        raise ProvisioningCancelledError()
    if result != 0:
        raise RobotPairingError(
            "Runtime pairing ended before the robot connected."
        )
    return result


async def _scan_setup_devices(
    provisioner: BluetoothProvisioner,
) -> list[BluetoothDevice]:
    print(
        "Scanning for nearby WatcheRobot devices "
        "(up to 10 seconds)",
        end="",
        flush=True,
    )
    scan_task = asyncio.create_task(provisioner.scan_devices())
    try:
        if not _is_interactive_terminal():
            return await scan_task
        while not scan_task.done():
            done, _pending = await asyncio.wait(
                {scan_task},
                timeout=_SETUP_SCAN_PROGRESS_INTERVAL_SECONDS,
            )
            if scan_task in done:
                break
            print(".", end="", flush=True)
        return await scan_task
    finally:
        print()


def _confirm_robot_wifi_connected() -> None:
    print()
    print("This does not confirm that the password is correct yet.")
    print("The robot will disconnect Bluetooth and try the Wi-Fi network.")
    print()
    print("Check Settings > Wi-Fi on the robot:")
    print("  - Connected: continue to the pairing step.")
    print(
        "  - Offline or Wi-Fi failed: the Wi-Fi name or password may be "
        "incorrect."
    )
    print(
        "    Disconnect/forget that network on the robot, reopen "
        "Settings > Wi-Fi, then rerun watcherobot robot setup."
    )
    if not _is_interactive_terminal():
        return
    try:
        input("Press Enter after the robot shows Wi-Fi Connected: ")
    except (EOFError, KeyboardInterrupt) as exc:
        raise ProvisioningCancelledError() from exc


def _redact_pairing_codes(message: str) -> str:
    return _PAIRING_CODE_IN_TEXT.sub("<pairing-code>", message)


def _prepare_robot_for_setup(*, wait_for_confirmation: bool) -> None:
    print("Prepare the robot for first-time setup:")
    print("  1. Turn on Bluetooth on this computer.")
    print("  2. Turn on the robot and open Settings > Wi-Fi.")
    print("  3. Keep that page open so the robot can advertise over Bluetooth.")
    print(
        "  4. Already on Wi-Fi? Press Ctrl+C, open the robot's "
        '"Python SDK" app, and run watcherobot robot pair <code>.'
    )
    print()
    if not wait_for_confirmation:
        return
    try:
        input("Press Enter after opening Settings > Wi-Fi on the robot: ")
    except EOFError as exc:
        raise ProvisioningCancelledError() from exc


def _select_setup_device(
    devices: list[BluetoothDevice],
    *,
    requested_id: str | None,
) -> BluetoothDevice:
    if not devices:
        raise DeviceNotFoundError(
            "No WatcheRobot was found. Keep the robot on Settings > Wi-Fi "
            "so Bluetooth advertising is enabled, keep it nearby, and retry."
        )
    if requested_id:
        matches = [
            device
            for device in devices
            if _setup_device_matches_identifier(device, requested_id)
        ]
        if not matches:
            raise DeviceNotFoundError(
                f"Robot {requested_id!r} was not found; scan again"
            )
        if len(matches) > 1:
            raise DeviceAmbiguityError(
                f"Robot identifier {requested_id!r} is ambiguous"
            )
        print(f"Selected {_setup_device_identity(matches[0])}")
        return matches[0]
    if len(devices) == 1:
        device = devices[0]
        print("Found one robot.")
        print(_setup_device_identity(device))
        return device
    if not _is_interactive_terminal():
        raise RobotSetupError(
            "Multiple robots were found; rerun with "
            "--device <Device ID or legacy Bluetooth ID>"
        )
    return _select_setup_device_with_arrows(devices)


def _select_setup_device_with_arrows(
    devices: list[BluetoothDevice],
) -> BluetoothDevice:
    selected_index = 0
    first_render = True
    print("Found multiple robots.")
    print("Select a robot with Up/Down, then press Enter:")
    while True:
        if not first_render:
            print(f"\x1b[{len(devices)}A", end="")
        for index, device in enumerate(devices):
            marker = ">" if index == selected_index else " "
            print(f"\r\x1b[2K {marker} {_setup_device_identity(device)}")
        first_render = False
        key = _read_setup_menu_key()
        if key == "up":
            selected_index = (selected_index - 1) % len(devices)
        elif key == "down":
            selected_index = (selected_index + 1) % len(devices)
        elif key == "select":
            print(
                "Selected "
                f"{_setup_device_identity(devices[selected_index])}"
            )
            return devices[selected_index]
        elif key == "cancel":
            raise KeyboardInterrupt


def _setup_device_identity(device: BluetoothDevice) -> str:
    if device.device_id is not None:
        return f"Device ID: {device.device_id}"
    return (
        "Device ID unavailable - firmware update may be required "
        f"(Bluetooth ID: {device.id})"
    )


def _setup_device_matches_identifier(
    device: BluetoothDevice,
    requested_id: str,
) -> bool:
    normalized = requested_id.casefold()
    return any(
        candidate is not None and candidate.casefold() == normalized
        for candidate in (device.device_id, device.id)
    )


def _read_setup_menu_key() -> str:
    if os.name == "nt":
        return _read_windows_setup_menu_key()
    return _read_posix_setup_menu_key()


def _read_windows_setup_menu_key() -> str:
    import msvcrt

    getwch = getattr(msvcrt, "getwch")
    key = getwch()
    if key in {"\x00", "\xe0"}:
        extended_key = getwch()
        return {"H": "up", "P": "down"}.get(extended_key, "other")
    if key == "\r":
        return "select"
    if key == "\x03":
        return "cancel"
    return "other"


def _read_posix_setup_menu_key() -> str:
    import termios
    import tty

    file_descriptor = sys.stdin.fileno()
    tcgetattr = getattr(termios, "tcgetattr")
    tcsetattr = getattr(termios, "tcsetattr")
    setraw = getattr(tty, "setraw")
    drain_mode = getattr(termios, "TCSADRAIN")
    previous_settings = tcgetattr(file_descriptor)
    try:
        setraw(file_descriptor)
        key = sys.stdin.read(1)
        if key == "\x1b":
            sequence = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(sequence, "other")
        if key in {"\r", "\n"}:
            return "select"
        if key == "\x03":
            return "cancel"
        return "other"
    finally:
        tcsetattr(
            file_descriptor,
            drain_mode,
            previous_settings,
        )


def _setup_text_value(
    value: object,
    *,
    prompt: str,
    field_name: str,
    default: str | None = None,
) -> str:
    if _has_text(value):
        return str(value).strip()
    if not _is_interactive_terminal():
        raise RobotSetupError(
            f"{field_name} is required in non-interactive use"
        )
    try:
        supplied = input(prompt).strip()
    except EOFError as exc:
        raise ProvisioningCancelledError() from exc
    resolved = supplied or default
    if not resolved:
        raise RobotSetupError(f"{field_name} is required")
    return resolved


def robot_status() -> int:
    state = _live_runtime_state()
    if state is None:
        _print_robot_disconnected("Runtime is not running")
        return 1
    payload = _request_json(state.control_url, "/daemon/devices")
    device = _device_from_payload(payload)
    if bool(device.get("online")):
        print("Robot connected")
        print()
        print(f"State:  {device.get('state', 'connected')}")
        print(f"Mode:   {device.get('mode') or 'unknown'}")
        return 0
    _print_robot_disconnected(str(device.get("state") or "unknown"))
    return 1


def pair_robot(
    pairing_code: str,
    *,
    timeout: float = ROBOT_PAIR_TIMEOUT_SECONDS,
) -> int:
    state, _reused = ensure_runtime()
    device = _device_from_payload(
        _request_json(state.control_url, "/daemon/devices")
    )
    if bool(device.get("online")):
        print("Robot is already connected.")
        return 0
    if str(device.get("state") or "idle") != "idle":
        raise CliError(
            "Robot pairing is already in progress; run "
            "'watcherobot robot status' and retry when it is idle"
        )

    _request_json(
        state.control_url,
        "/daemon/devices/pair",
        method="POST",
        payload={
            "pairing_code": pairing_code,
            "target_mode": "python_sdk",
        },
    )
    print("Pairing with the robot...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        device = _device_from_payload(
            _request_json(state.control_url, "/daemon/devices")
        )
        if bool(device.get("online")):
            print("Robot connected successfully.")
            return 0
        pairing_state = str(device.get("state") or "unknown")
        last_error = device.get("last_error")
        if pairing_state == "idle" and last_error:
            raise CliError(_pairing_error_message(str(last_error)))
        time.sleep(0.25)
    raise CliError(
        "Robot pairing timed out. Confirm that the robot and this computer "
        "are on the same network, then run 'watcherobot robot pair' again."
    )


def _device_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    device = payload.get("device")
    if not isinstance(device, dict):
        raise CliError("Runtime returned an invalid robot status")
    return device


def _pairing_error_message(error: str) -> str:
    messages = {
        "pairing_not_found": (
            "Robot was not found. Confirm that both devices are on the same "
            "network and that the 6-digit code is still visible."
        ),
        "device_connect_timeout": (
            "The robot was found but did not finish connecting. Retry with "
            "the latest 6-digit code."
        ),
        "device_busy": "The robot is already paired with another Runtime.",
    }
    return messages.get(error, f"Robot pairing failed: {error}")


def _print_robot_disconnected(state: str) -> None:
    print("Robot is not connected")
    print()
    print(f"State:  {state}")
    print("Next:   watcherobot robot setup")


async def _run_bluetooth_command(args: argparse.Namespace) -> int:
    provisioner = BluetoothProvisioner()
    if args.bluetooth_command == "scan":
        devices = await provisioner.scan_devices()
        _print_json(
            {"devices": [device.to_dict() for device in devices]}
        )
        return 0

    device = await _resolve_bluetooth_device(
        provisioner,
        str(args.device),
    )
    if args.bluetooth_command == "provision":
        password = getpass("Wi-Fi password: ")
        try:
            result = await provisioner.provision_wifi(
                device,
                ssid=str(args.ssid),
                password=password,
                clear_existing=bool(args.clear_existing),
            )
        finally:
            del password
        _print_json(result.to_dict())
        return 0
    if args.bluetooth_command == "status":
        status = await provisioner.get_wifi_status(device)
        _print_json(status.to_dict())
        return 0
    if args.bluetooth_command == "clear":
        status = await provisioner.clear_wifi(device)
        _print_json(status.to_dict())
        return 0
    raise CliError("unsupported bluetooth command")


async def _resolve_bluetooth_device(
    provisioner: BluetoothProvisioner,
    device_id: str,
) -> BluetoothDevice:
    devices = await provisioner.scan_devices()
    matches = [device for device in devices if device.id == device_id]
    if not matches:
        raise DeviceNotFoundError(
            f"Bluetooth device {device_id!r} was not found; scan again"
        )
    if len(matches) > 1:
        raise DeviceAmbiguityError(
            f"Bluetooth device identifier {device_id!r} is ambiguous"
        )
    return matches[0]


def runtime_status() -> dict[str, Any]:
    state = _live_runtime_state()
    if state is None:
        return {"running": False}
    status = _request_json(state.control_url, "/daemon/status")
    return {
        "running": True,
        "pid": state.pid,
        "control_url": state.control_url,
        **status,
    }


def ensure_runtime(
    *,
    state_root: Path | None = None,
    managed_app_root: Path | None = None,
    ephemeral_ports: bool = False,
) -> tuple[RuntimeProcessState, bool]:
    resolved_state_root = (state_root or default_runtime_state_root()).resolve()
    existing = _live_runtime_state(resolved_state_root)
    if existing is not None:
        return existing, True

    resolved_state_root.mkdir(parents=True, exist_ok=True)
    log_path = resolved_state_root / "runtime.log"
    daemon_python = _canonical_launcher_path(Path(sys.executable))
    command = [
        os.fspath(_background_python_executable(daemon_python)),
        "-m",
        "watcherobot.runtime.daemon",
        "--state-root",
        str(resolved_state_root),
    ]
    if managed_app_root is not None:
        command.extend(("--managed-app-root", str(managed_app_root.resolve())))
    else:
        command.extend(
            ("--managed-app-root", str(daemon_python.parent))
        )
    if ephemeral_ports:
        command.extend(
            (
                "--control-port",
                "0",
                "--external-port",
                "0",
                "--pairing-port",
                "0",
                "--preview-udp-port",
                "0",
            )
        )
    creation_flags = 0
    process_options: dict[str, Any] = {}
    if os.name == "nt":
        creation_flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
            | getattr(subprocess, "DETACHED_PROCESS")
            | getattr(subprocess, "CREATE_NO_WINDOW")
        )
    else:
        process_options["start_new_session"] = True

    with log_path.open("ab") as log_file:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            close_fds=True,
            creationflags=creation_flags,
            **process_options,
        )

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        state = _live_runtime_state(resolved_state_root)
        if state is not None:
            return state, False
        time.sleep(0.05)
    details = ""
    try:
        details = log_path.read_text(encoding="utf-8", errors="replace")[-1000:]
    except OSError:
        pass
    raise CliError(f"Runtime failed to start. {details}".strip())


def _background_python_executable(
    executable: Path,
    *,
    is_windows: bool | None = None,
) -> Path:
    """Select a Python interpreter that cannot allocate a Windows terminal."""

    launcher = _canonical_launcher_path(executable)
    running_on_windows = os.name == "nt" if is_windows is None else is_windows
    if not running_on_windows or launcher.name.lower() != "python.exe":
        return launcher
    pythonw = launcher.with_name("pythonw.exe")
    if pythonw.is_file():
        return pythonw
    return launcher


def _canonical_launcher_path(executable: Path) -> Path:
    """Canonicalize the parent without losing a virtualenv launcher identity."""

    requested = Path(executable)
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    return requested.parent.resolve(strict=True) / requested.name


def stop_runtime() -> None:
    state = _live_runtime_state()
    if state is None:
        RuntimeStateStore(default_runtime_state_root()).remove()
        return
    _request_json(state.control_url, "/daemon/stop", method="POST")
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _live_runtime_state() is None:
            return
        time.sleep(0.05)
    raise CliError("Runtime did not stop within 10 seconds")


def run_application(application: Path) -> int:
    application_path = Path(application).resolve()
    if not application_path.is_dir():
        raise CliError(
            f"Application directory does not exist: {application_path}"
        )
    manifest = ApplicationManifest.load(application_path)
    application_log_path = (
        default_runtime_state_root()
        / "logs"
        / "applications"
        / f"{manifest.app_id}.jsonl"
    )
    application_log_offset = _file_size(application_log_path)
    print(f"Running Application: {application_path}")
    print("Press Ctrl+C to stop.")
    state, _reused = ensure_runtime()
    _print_application_robot_guidance(state.control_url)
    _request_json(
        state.control_url,
        "/daemon/application/select",
        method="POST",
        payload={
            "application_dir": str(application_path),
            "launcher": {
                "kind": "python",
                "executable": str(
                    _canonical_launcher_path(Path(sys.executable))
                ),
            },
        },
    )
    _request_json(
        state.control_url,
        "/daemon/application/start",
        method="POST",
        timeout=APPLICATION_START_TIMEOUT_SECONDS,
    )
    try:
        while True:
            application_log_offset = _print_application_logs(
                application_log_path,
                after_offset=application_log_offset,
            )
            status = _request_json(state.control_url, "/daemon/status")
            application_status = status["application"]
            if application_status["state"] in {"ended", "error"}:
                _print_application_logs(
                    application_log_path,
                    after_offset=application_log_offset,
                )
                final_state = str(application_status["state"])
                print(f"Application finished: {final_state}")
                return 0 if final_state == "ended" else 1
            time.sleep(0.1)
    except KeyboardInterrupt:
        _request_json(
            state.control_url,
            "/daemon/application/stop",
            method="POST",
        )
        print("Application stopped by user.")
        return 130


def _print_application_robot_guidance(control_url: str) -> None:
    try:
        device = _device_from_payload(
            _request_json(control_url, "/daemon/devices")
        )
    except CliError:
        return
    if bool(device.get("online")):
        return
    print()
    print("No robot is connected. This Application can still run offline.")
    print("First-time setup: watcherobot robot setup")
    print(
        'Already on Wi-Fi: open the robot\'s "Python SDK" app, then run '
        "watcherobot robot pair <code>"
    )
    print()


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _print_application_logs(path: Path, *, after_offset: int) -> int:
    """Print newly persisted Application process logs and return the next offset."""

    try:
        with path.open("rb") as log_file:
            size = log_file.seek(0, os.SEEK_END)
            if size < after_offset:
                after_offset = 0
            log_file.seek(after_offset)
            payload = log_file.read()
    except OSError:
        return after_offset

    last_newline = payload.rfind(b"\n")
    if last_newline < 0:
        return after_offset
    complete_payload = payload[: last_newline + 1]
    next_offset = after_offset + len(complete_payload)

    for raw_line in complete_payload.splitlines():
        try:
            record = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        stream = str(record.get("stream") or "stdout")
        message = str(record.get("message") or "")
        if message:
            print(f"Application {stream}: {message}")
    return next_offset


def run_installed_application(*, store_root: Path, application_id: str) -> int:
    resolved_store_root = Path(store_root).resolve()
    try:
        applications = list_installed_applications(resolved_store_root)
    except ApplicationInstallError as exc:
        raise CliError(str(exc)) from exc
    application = next(
        (
            item
            for item in applications
            if item.application_id == application_id and item.status == "installed"
        ),
        None,
    )
    if application is None:
        raise CliError(
            "Installed Application was not found or is not ready to run: "
            f"{application_id}"
        )

    print(f"Running installed Application: {application.name}")
    print(f"Application store: {resolved_store_root}")
    state, _reused = ensure_runtime(
        state_root=resolved_store_root / ".daemon-session",
        managed_app_root=resolved_store_root,
        ephemeral_ports=True,
    )
    print(f"Daemon external URL: {state.external_url}")
    print("Press Ctrl+C to stop.")
    _request_json(
        state.control_url,
        "/daemon/application/select",
        method="POST",
        payload={
            "application_dir": str(application.application_root / "source"),
            "launcher": {
                "kind": "python",
                "executable": str(
                    application.application_root / ".venv" / _python_executable_name()
                ),
            },
        },
    )
    _request_json(
        state.control_url,
        "/daemon/application/start",
        method="POST",
        timeout=APPLICATION_START_TIMEOUT_SECONDS,
    )
    return _wait_for_application_completion(state.control_url)


def _python_executable_name() -> str:
    return "Scripts/python.exe" if os.name == "nt" else "bin/python"


def _wait_for_application_completion(control_url: str) -> int:
    try:
        while True:
            status = _request_json(control_url, "/daemon/status")
            application_status = status["application"]
            if application_status["state"] in {"ended", "error"}:
                final_state = str(application_status["state"])
                print(f"Application finished: {final_state}")
                return 0 if final_state == "ended" else 1
            time.sleep(0.1)
    except KeyboardInterrupt:
        _request_json(
            control_url,
            "/daemon/application/stop",
            method="POST",
        )
        print("Application stopped by user.")
        return 130


def _live_runtime_state(
    state_root: Path | None = None,
) -> RuntimeProcessState | None:
    store = RuntimeStateStore(state_root or default_runtime_state_root())
    state = store.read()
    if state is None:
        return None
    try:
        _request_json(state.control_url, "/daemon/status", timeout=0.5)
    except CliError:
        return None
    return state


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8")).get(
                "message",
                str(exc),
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = str(exc)
        raise CliError(str(message)) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise CliError(f"Runtime request failed: {exc}") from exc
    if not isinstance(decoded, dict):
        raise CliError("Runtime returned an invalid response")
    return decoded


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _print_application_runtime_result(
    title: str,
    payload: dict[str, Any],
) -> None:
    application = payload.get("application")
    if not isinstance(application, dict):
        _print_json(payload)
        return
    print(title)
    print()
    print(f"ID:     {application.get('current_app', 'Unknown')}")
    print(f"State:  {application.get('state', 'unknown')}")
    process_id = application.get("process_id")
    if process_id is not None:
        print(f"PID:    {process_id}")
