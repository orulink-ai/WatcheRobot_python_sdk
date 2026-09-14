"""Short-lived shared Daemon launcher. The Daemon retains the lifetime lock."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .daemon.instance import default_runtime_instance_root, default_runtime_state_root
from .repository import operation_lock

_LAUNCH_ENVIRONMENT = (
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONPATH",
    "WATCHER_SERVER_PROJECT_ROOT",
    "WATCHER_SERVER_ROOT",
    "WATCHER_SERVER_SOURCE_ROOT",
    "WATCHER_SERVER_SOURCE_KIND",
    "WATCHER_SERVER_CONFIG_ROOT",
    "WATCHER_SERVER_DATA_DIR",
    "WATCHER_SERVER_LOG_DIR",
    "WATCHER_WEATHER_MCP_COMMAND",
)


def read_launcher(pointer: Path) -> tuple[list[str], dict[str, str]]:
    saved = json.loads(pointer.read_text(encoding="utf-8"))
    command = saved.get("command") if isinstance(saved, dict) else saved
    environment = saved.get("environment", {}) if isinstance(saved, dict) else {}
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(v, str) for v in command)
    ):
        raise ValueError(
            "Invalid shared Runtime launcher; explicit activation required"
        )
    if not Path(command[0]).is_file():
        raise ValueError(
            "Shared Runtime launcher is missing; explicit activation required"
        )
    if not isinstance(environment, dict) or any(
        k not in _LAUNCH_ENVIRONMENT or not isinstance(v, str)
        for k, v in environment.items()
    ):
        raise ValueError("Invalid shared Runtime launcher environment")
    return command, environment


def save_launcher(
    command: list[str], environment: dict[str, str] | None = None
) -> None:
    """Commit the launcher while the caller holds the operation lock."""
    pointer = default_runtime_instance_root() / "current-launcher.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    captured = (
        environment
        if environment is not None
        else {key: os.environ[key] for key in _LAUNCH_ENVIRONMENT if key in os.environ}
    )
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"command": command, "environment": captured}), encoding="utf-8"
    )
    temporary.replace(pointer)


def ensure_command(command: list[str], *, activate: bool = False) -> None:
    """Reuse the live instance; remember the last successful launcher across clients.

    Activation is explicit and requires the current application to be stopped first.
    The pointer is committed only after the target has answered its status endpoint.
    Only the documented path/configuration environment whitelist is persisted.
    """
    from watcherobot.cli import _live_runtime_state, _request_json, stop_runtime

    root = default_runtime_instance_root()
    pointer = root / "current-launcher.json"
    with operation_lock():
        try:
            previous = read_launcher(pointer) if pointer.is_file() else None
        except (OSError, ValueError):
            if not activate:
                raise
            previous = None
        live = _live_runtime_state()
        if live is not None:
            if not activate:
                return
            status = _request_json(live.control_url, "/daemon/status")
            import psutil

            previous_processes = []
            try:
                previous_process = psutil.Process(live.pid)
                previous_processes.append(previous_process)
                parent = previous_process.parent()
                if (
                    parent is not None
                    and status.get("runtime", {}).get("source") == "bundle"
                    and parent.exe() == previous_process.exe()
                ):
                    previous_processes.append(parent)
            except psutil.NoSuchProcess:
                pass
            if status.get("application", {}).get("state") in (
                "starting",
                "running",
                "stopping",
            ):
                raise RuntimeError(
                    "Stop the current Application before switching Runtime"
                )
            if status.get("runtime", {}).get("management_protocol") == 1:
                _request_json(live.control_url, "/daemon/prepare-update", method="POST")
            if not stop_runtime():
                raise RuntimeError(
                    "Previous Runtime has not exited; activation cancelled"
                )
            _, alive = psutil.wait_procs(previous_processes, timeout=15)
            if alive:
                raise RuntimeError(
                    "Previous Runtime process still holds files; activation cancelled"
                )
        selected = command
        launch_environment = {
            key: os.environ[key] for key in _LAUNCH_ENVIRONMENT if key in os.environ
        }
        if not activate and pointer.is_file():
            selected, launch_environment = read_launcher(pointer)
        log_root = default_runtime_state_root()
        log_root.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {}
        environment = dict(os.environ)
        for key in _LAUNCH_ENVIRONMENT:
            environment.pop(key, None)
        environment.update(launch_environment)
        launch_id = uuid.uuid4().hex
        environment["WATCHER_RUNTIME_LAUNCH_ID"] = launch_id
        if os.name == "nt":
            options["creationflags"] = (
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            )
        else:
            options["start_new_session"] = True
        process = None
        try:
            with (log_root / "runtime.log").open("ab") as log:
                process = subprocess.Popen(
                    selected,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    close_fds=True,
                    env=environment,
                    **options,
                )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                live = _live_runtime_state()
                if live is not None:
                    status = _request_json(live.control_url, "/daemon/status")
                    if status.get("runtime", {}).get("launch_id") != launch_id:
                        raise RuntimeError(
                            "Another launcher won the Runtime lock; activation was not committed"
                        )
                    save_launcher(selected, launch_environment)
                    return
                if process.poll() is not None:
                    raise RuntimeError(
                        "Runtime launcher exited before readiness; see runtime.log"
                    )
                time.sleep(0.05)
            raise RuntimeError(
                "Runtime readiness timed out; see runtime.log before retrying"
            )
        except Exception:
            # Reap only our own candidate, including frozen/venv child processes.
            import psutil

            if process is not None and process.poll() is None:
                try:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    for child in reversed(children):
                        child.terminate()
                    parent.terminate()
                    psutil.wait_procs([parent, *children], timeout=10)
                except psutil.NoSuchProcess:
                    pass
            if activate and previous is not None and _live_runtime_state() is None:
                # The pointer still describes the previous healthy deployment.
                # A subsequent ensure uses it, rather than the failed candidate.
                old_command, old_environment = previous
                for key in _LAUNCH_ENVIRONMENT:
                    environment.pop(key, None)
                environment.update(old_environment)
                environment.pop("WATCHER_RUNTIME_LAUNCH_ID", None)
                with (log_root / "runtime.log").open("ab") as log:
                    restored = subprocess.Popen(
                        old_command,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=log,
                        close_fds=True,
                        env=environment,
                        **options,
                    )
                deadline = time.monotonic() + 30
                while _live_runtime_state() is None:
                    if restored.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Activation failed and previous Runtime could not recover; see runtime.log"
                        )
                    time.sleep(0.05)
            raise
