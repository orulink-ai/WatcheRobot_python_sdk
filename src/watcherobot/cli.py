"""Command-line entrypoint for the SDK-owned Runtime and Applications."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from getpass import getpass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
    BluetoothDevice,
    BluetoothProvisioner,
    BluetoothProvisioningError,
    DeviceAmbiguityError,
    DeviceNotFoundError,
    ProvisioningCancelledError,
)
from watcherobot.runtime.daemon.application.manifest import ApplicationManifest
from watcherobot.runtime.daemon.instance import (
    RuntimeProcessState,
    RuntimeStateStore,
    default_runtime_state_root,
)


APPLICATION_START_TIMEOUT_SECONDS = 90.0


class CliError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watcherobot",
        description="WatcheRobot SDK command-line tools.",
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
