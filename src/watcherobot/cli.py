"""Command-line entrypoint for the SDK-owned Runtime and Applications."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from watcherobot.application.catalog import package_application
from watcherobot.runtime.daemon.instance import (
    RuntimeProcessState,
    RuntimeStateStore,
    default_runtime_state_root,
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
    except CliError as exc:
        print(
            json.dumps({"error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    raise CliError("unsupported command")


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
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
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
