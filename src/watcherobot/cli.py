"""Command-line entrypoint for the SDK-owned Runtime and Applications."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from watcherobot.application.catalog import package_application
from watcherobot.distribution.check import check_application
from watcherobot.distribution.credentials import SystemCredentialStore
from watcherobot.distribution.events import (
    DistributionEvent,
    ErrorCode,
    ErrorEvent,
    EventSink,
    ExitCode,
    JsonLineEventWriter,
    ProgressEvent,
    ResultEvent,
    exit_code_for,
)
from watcherobot.distribution.hub_http import HuggingFaceHubClient
from watcherobot.distribution.login import (
    LoginError,
    LoginResult,
    LoginStatus,
    login,
    login_status,
    logout,
)
from watcherobot.distribution.oauth_http import HuggingFaceOAuthClient
from watcherobot.distribution.ports import CredentialStore, HubClient, OAuthClient
from watcherobot.distribution.source_files import ApplicationSourceError
from watcherobot.provisioning import (
    BluetoothDevice,
    BluetoothProvisioner,
    BluetoothProvisioningError,
    DeviceAmbiguityError,
    DeviceNotFoundError,
    ProvisioningCancelledError,
)
from watcherobot.runtime.daemon.instance import (
    RuntimeProcessState,
    RuntimeStateStore,
    default_runtime_state_root,
)
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationCompatibilityError,
    ApplicationManifestError,
)


class CliError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="watcherobot")
    commands = parser.add_subparsers(dest="command", required=True)

    daemon = commands.add_parser("daemon")
    daemon_commands = daemon.add_subparsers(
        dest="daemon_command",
        required=True,
    )
    daemon_commands.add_parser("start")
    daemon_commands.add_parser("status")
    daemon_commands.add_parser("stop")

    app = commands.add_parser("app")
    app_commands = app.add_subparsers(dest="app_command", required=True)
    run = app_commands.add_parser("run")
    run.add_argument("application", type=Path)
    package = app_commands.add_parser("package")
    package.add_argument("application_dir", type=Path)
    package.add_argument("output", type=Path)
    check = app_commands.add_parser("check")
    check.add_argument("application_dir", type=Path)
    check.add_argument("--jsonl", action="store_true")
    login_command = app_commands.add_parser("login")
    login_mode = login_command.add_mutually_exclusive_group()
    login_mode.add_argument("--status", action="store_true")
    login_mode.add_argument("--force", action="store_true")
    login_command.add_argument("--jsonl", action="store_true")
    logout_command = app_commands.add_parser("logout")
    logout_command.add_argument("--jsonl", action="store_true")
    install = app_commands.add_parser("install")
    install.add_argument("package", type=Path)
    app_commands.add_parser("list")
    select = app_commands.add_parser("select")
    select.add_argument("app_id")
    select.add_argument("--version")
    app_commands.add_parser("start")
    app_commands.add_parser("stop")
    uninstall = app_commands.add_parser("uninstall")
    uninstall.add_argument("app_id")
    uninstall.add_argument("--version")

    bluetooth = commands.add_parser("bluetooth")
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
    args = build_parser().parse_args(argv)
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
        if args.command == "app" and args.app_command == "check":
            return _run_application_check(args)
        if args.command == "app" and args.app_command == "login":
            return _run_application_login(args)
        if args.command == "app" and args.app_command == "logout":
            return _run_application_logout(args)
        if args.command == "app":
            if args.app_command == "package":
                output = package_application(
                    args.application_dir,
                    args.output,
                )
                _print_json({"package": str(output)})
                return 0
            state, _reused = ensure_runtime()
            if args.app_command == "install":
                result = _request_json(
                    state.control_url,
                    "/daemon/applications/install",
                    method="POST",
                    payload={"package_path": str(args.package.resolve())},
                )
                _print_json(result)
                return 0
            if args.app_command == "list":
                _print_json(
                    _request_json(
                        state.control_url,
                        "/daemon/applications",
                    )
                )
                return 0
            if args.app_command == "select":
                result = _request_json(
                    state.control_url,
                    "/daemon/applications/select",
                    method="POST",
                    payload={
                        "app_id": args.app_id,
                        "version": args.version,
                    },
                )
                _print_json(result)
                return 0
            if args.app_command == "start":
                _print_json(
                    _request_json(
                        state.control_url,
                        "/daemon/application/start",
                        method="POST",
                    )
                )
                return 0
            if args.app_command == "stop":
                _print_json(
                    _request_json(
                        state.control_url,
                        "/daemon/application/stop",
                        method="POST",
                    )
                )
                return 0
            if args.app_command == "uninstall":
                result = _request_json(
                    state.control_url,
                    "/daemon/applications/uninstall",
                    method="POST",
                    payload={
                        "app_id": args.app_id,
                        "version": args.version,
                    },
                )
                _print_json(result)
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
    except (CliError, BluetoothProvisioningError) as exc:
        print(
            json.dumps({"error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    raise CliError("unsupported command")


def _run_application_check(args: argparse.Namespace) -> int:
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    if event_writer is not None:
        event_writer.emit(
            ProgressEvent(
                stage="checking",
                message="正在检查 Application",
            )
        )
    try:
        result = check_application(args.application_dir)
    except ApplicationCompatibilityError as exc:
        return _print_application_check_error(
            ErrorCode.APP_SDK_INCOMPATIBLE,
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationManifestError as exc:
        return _print_application_check_error(
            _manifest_error_code(exc),
            str(exc),
            event_writer=event_writer,
        )
    except ApplicationSourceError as exc:
        return _print_application_check_error(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            str(exc),
            event_writer=event_writer,
        )

    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    else:
        print(f"Application 有效：{result.app_id}@{result.version}")
    return ExitCode.SUCCESS


@dataclass(frozen=True)
class _AuthDependencies:
    oauth: OAuthClient
    credentials: CredentialStore
    hub: HubClient


class _HumanAuthEventSink:
    """Render public Device Flow instructions for terminal users."""

    def emit(self, event: DistributionEvent) -> None:
        if not isinstance(event, ProgressEvent):
            return
        print(event.message)
        verification_uri = event.data.get("verification_uri")
        user_code = event.data.get("user_code")
        expires_in = event.data.get("expires_in")
        if isinstance(verification_uri, str):
            print(f"打开：{verification_uri}")
        if isinstance(user_code, str):
            print(f"输入验证码：{user_code}")
        if isinstance(expires_in, int):
            print(f"验证码有效期：{expires_in} 秒")


def _build_auth_dependencies() -> _AuthDependencies:
    return _AuthDependencies(
        oauth=HuggingFaceOAuthClient(),
        credentials=SystemCredentialStore(),
        hub=HuggingFaceHubClient(),
    )


def _run_application_login(args: argparse.Namespace) -> int:
    dependencies = _build_auth_dependencies()
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    result: LoginStatus | LoginResult
    try:
        if args.status:
            result = login_status(
                credentials=dependencies.credentials,
                hub=dependencies.hub,
            )
        else:
            events: EventSink = event_writer or _HumanAuthEventSink()
            result = login(
                oauth=dependencies.oauth,
                credentials=dependencies.credentials,
                hub=dependencies.hub,
                events=events,
                force=bool(args.force),
            )
    except KeyboardInterrupt:
        return _print_auth_error(
            ErrorCode.OPERATION_CANCELLED,
            "Hugging Face 登录已取消",
            event_writer=event_writer,
        )
    except LoginError as exc:
        return _print_auth_error(
            exc.code,
            str(exc),
            event_writer=event_writer,
        )

    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    elif isinstance(result, LoginStatus):
        if result.logged_in:
            print(f"已登录 Hugging Face：{result.username}")
        else:
            print("尚未登录 Hugging Face")
    elif result.reused:
        print(f"已使用现有 Hugging Face 登录：{result.username}")
    else:
        print(f"Hugging Face 登录成功：{result.username}")
    return ExitCode.SUCCESS


def _run_application_logout(args: argparse.Namespace) -> int:
    dependencies = _build_auth_dependencies()
    event_writer = JsonLineEventWriter(sys.stdout) if args.jsonl else None
    try:
        result = logout(credentials=dependencies.credentials)
    except LoginError as exc:
        return _print_auth_error(
            exc.code,
            str(exc),
            event_writer=event_writer,
        )
    if event_writer is not None:
        event_writer.emit(ResultEvent(data=result.to_dict()))
    else:
        print("已退出 Watcher 的 Hugging Face 登录")
    return ExitCode.SUCCESS


def _print_auth_error(
    code: ErrorCode,
    message: str,
    *,
    event_writer: JsonLineEventWriter | None,
) -> int:
    if event_writer is not None:
        event_writer.emit(ErrorEvent(code=code, message=message))
    else:
        print(message, file=sys.stderr)
    return exit_code_for(code)


def _print_application_check_error(
    code: ErrorCode,
    message: str,
    *,
    event_writer: JsonLineEventWriter | None,
) -> int:
    if event_writer is not None:
        event_writer.emit(ErrorEvent(code=code, message=message))
    else:
        print(f"Application 检查失败：{message}", file=sys.stderr)
    return exit_code_for(code)


def _manifest_error_code(error: ApplicationManifestError) -> ErrorCode:
    try:
        return ErrorCode(error.code)
    except ValueError:
        return ErrorCode.APP_MANIFEST_INVALID


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


def ensure_runtime() -> tuple[RuntimeProcessState, bool]:
    existing = _live_runtime_state()
    if existing is not None:
        return existing, True

    state_root = default_runtime_state_root()
    state_root.mkdir(parents=True, exist_ok=True)
    log_path = state_root / "runtime.log"
    command = [
        sys.executable,
        "-m",
        "watcherobot.runtime.daemon",
        "--state-root",
        str(state_root),
    ]
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
        state = _live_runtime_state()
        if state is not None:
            return state, False
        time.sleep(0.05)
    details = ""
    try:
        details = log_path.read_text(encoding="utf-8", errors="replace")[-1000:]
    except OSError:
        pass
    raise CliError(f"Runtime failed to start. {details}".strip())


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
    state, _reused = ensure_runtime()
    application_path = Path(application).resolve()
    if application_path.suffix.lower() == ".wapp":
        installed = _request_json(
            state.control_url,
            "/daemon/applications/install",
            method="POST",
            payload={"package_path": str(application_path)},
        )["application"]
        _request_json(
            state.control_url,
            "/daemon/applications/select",
            method="POST",
            payload={
                "app_id": installed["id"],
                "version": installed["version"],
            },
        )
    else:
        if not application_path.is_dir():
            raise CliError(
                f"Application directory does not exist: {application_path}"
            )
        _request_json(
            state.control_url,
            "/daemon/application/select",
            method="POST",
            payload={"application_dir": str(application_path)},
        )
    _request_json(
        state.control_url,
        "/daemon/application/start",
        method="POST",
    )
    try:
        while True:
            status = _request_json(state.control_url, "/daemon/status")
            application_status = status["application"]
            if application_status["state"] in {"ended", "error"}:
                return 0 if application_status["state"] == "ended" else 1
            time.sleep(0.1)
    except KeyboardInterrupt:
        _request_json(
            state.control_url,
            "/daemon/application/stop",
            method="POST",
        )
        return 130


def _live_runtime_state() -> RuntimeProcessState | None:
    store = RuntimeStateStore(default_runtime_state_root())
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
