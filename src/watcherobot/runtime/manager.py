"""Short-lived shared Daemon launcher. The Daemon retains the lifetime lock."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .daemon.instance import (
    RuntimeProcessState, default_runtime_instance_root, default_runtime_state_root,
)
from .repository import operation_lock
from .cleanup import cleanup_after
from .background_process import CREATE_NO_WINDOW, background_process_options


class RuntimeActivationCancelled(RuntimeError):
    """The requesting launcher explicitly cancelled its pending activation."""


def _check_activation_cancelled() -> None:
    marker = os.environ.get("WATCHER_RUNTIME_CANCEL_FILE")
    if marker and Path(marker).exists():
        raise RuntimeActivationCancelled("Runtime activation cancelled by launcher")


def describe_command(command: list[str]) -> dict[str, str]:
    """Exercise the candidate interpreter/imports before interrupting anything."""
    if (
        getattr(sys, "frozen", False)
        and command
        and Path(command[0]).resolve() == Path(sys.executable).resolve()
    ):
        # This frozen candidate has already booted and imported the Daemon.
        # Hash it here; a nested copy only repeats onefile extraction.
        from .identity import runtime_identity

        return runtime_identity()
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [*command, "--describe-runtime"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=True,
            **options,
        )
        identity = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            "Candidate Runtime validation failed; existing Runtime unchanged"
        ) from error
    if not isinstance(identity, dict) or not all(
        isinstance(identity.get(key), str) and identity[key]
        for key in ("sdk_version", "build_id")
    ):
        raise ValueError("Candidate Runtime did not report a valid build identity")
    return identity


def stop_shared_runtime() -> None:
    """Stop the currently verified instance, regardless of its original client."""
    with operation_lock(timeout=120):
        _stop_and_wait()


def _stop_and_wait() -> None:
    """Wait for both the Daemon and frozen bootloader to release their files.

    Capture process objects before shutdown so PID reuse cannot select a new
    process. No process is forcibly terminated from untrusted status metadata.
    """
    import psutil
    from urllib.parse import urlsplit
    from watcherobot.cli import CliError, _live_runtime_state, stop_runtime
    from .process_shutdown import capture_runtime, force_stop

    try:
        live = _live_runtime_state()
    except CliError:
        from .process_shutdown import stop_legacy_listener

        if stop_legacy_listener():
            return
        raise
    processes = []
    if live is not None:
        try:
            control_port = urlsplit(live.control_url).port
            if control_port is None:
                raise RuntimeError("Runtime control endpoint has no explicit port")
            processes = capture_runtime(live.pid, control_port)
        except psutil.NoSuchProcess:
            pass
    try:
        stopped = stop_runtime()
    except CliError:
        if not processes:
            raise
        stopped = False
    if not stopped and not processes:
        raise RuntimeError("Previous Runtime has not exited; inspect runtime.log")
    _, alive = psutil.wait_procs(processes, timeout=15 if stopped else 0)
    if alive:
        force_stop(alive)


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
    from .cleanup import reference_lock

    with reference_lock():
        _save_launcher(command, environment)


def _save_launcher(command: list[str], environment: dict[str, str] | None) -> None:
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
    if pointer.exists():
        try:
            old_command, old_environment = read_launcher(pointer)
        except (OSError, ValueError):
            old_command, old_environment = command, {}
        if old_command != command:
            old = {"command": old_command, "environment": old_environment}
            backup = pointer.with_name("previous-launcher.json")
            backup_temporary = backup.with_suffix(".tmp")
            backup_temporary.write_text(json.dumps(old), encoding="utf-8")
            backup_temporary.replace(backup)
    temporary.replace(pointer)


def _candidate_live_state() -> RuntimeProcessState | None:
    """Poll an already spawned candidate without mistaking bind for readiness.

    Pre-launch discovery remains strict. This probe never authorizes a process:
    the caller still verifies launch_id and build_id before committing activation.
    Transient probe errors keep waiting while the candidate remains alive.
    """
    from watcherobot.cli import CliError, _live_runtime_state

    try:
        return _live_runtime_state()
    except CliError:
        return None


@cleanup_after
def ensure_command(
    command: list[str], *, activate: bool = False, force: bool = False
) -> bool:
    """Reuse the live instance; remember the last successful launcher across clients.

    Activation is explicit and stops the current application on a version change or forced activation.
    The pointer is committed only after the target has answered its status endpoint.
    Only the documented path/configuration environment whitelist is persisted.
    """
    from watcherobot.cli import CliError, _live_runtime_state, _request_json
    from .cleanup import register_environment

    # Protect source venvs and default application resources before publication GC.
    if Path(command[0]).is_file():
        register_environment(Path(command[0]))

    root = default_runtime_instance_root()
    pointer = root / "current-launcher.json"
    with operation_lock(timeout=120):
        target_identity = describe_command(command) if activate else None
        try:
            previous = read_launcher(pointer) if pointer.is_file() else None
        except (OSError, ValueError):
            if not activate:
                raise
            previous = None
        try:
            live = _live_runtime_state()
        except CliError:
            if not activate:
                raise
            _stop_and_wait()
            live = _live_runtime_state()
        if live is not None:
            if not activate:
                return True
            status = _request_json(live.control_url, "/daemon/status")
            if (
                not force
                and target_identity is not None
                and status.get("runtime", {}).get("sdk_version")
                == target_identity["sdk_version"]
            ):
                return True
            # Shutdown closes admission and drains the managed Application.
            _stop_and_wait()
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
        # A detached frozen child must own its extraction directory. Otherwise
        # the short-lived manager can remove DLLs still used by the Daemon.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        launch_id = uuid.uuid4().hex
        environment["WATCHER_RUNTIME_LAUNCH_ID"] = launch_id
        options.update(background_process_options())
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
            # Cold extraction and file scanning have no predictable duration.
            # Fail on process exit or identity errors, not elapsed startup time.
            while True:
                _check_activation_cancelled()
                live = _candidate_live_state()
                if live is not None:
                    try:
                        status = _request_json(live.control_url, "/daemon/status")
                    except CliError:
                        time.sleep(0.05)
                        continue
                    if status.get("runtime", {}).get("launch_id") != launch_id:
                        raise RuntimeError(
                            "Another launcher won the Runtime lock; activation was not committed"
                        )
                    if (
                        target_identity is not None
                        and status.get("runtime", {}).get("build_id")
                        != target_identity["build_id"]
                    ):
                        raise RuntimeError(
                            "Runtime build changed between validation and readiness"
                        )
                    save_launcher(selected, launch_environment)
                    return False
                if process.poll() is not None:
                    raise RuntimeError(
                        "Runtime launcher exited before readiness; see runtime.log"
                    )
                time.sleep(0.05)
        except Exception as error:
            # Reap only our own candidate, including frozen/venv child processes.
            import psutil

            if process is not None and process.poll() is None:
                try:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    for child in reversed(children):
                        child.terminate()
                    parent.terminate()
                    _, alive = psutil.wait_procs([parent, *children], timeout=10)
                    for remaining in alive:
                        remaining.kill()
                    _, alive = psutil.wait_procs(alive, timeout=5)
                    if alive:
                        raise RuntimeError(
                            "Failed candidate still running; rollback cancelled"
                        )
                except psutil.NoSuchProcess:
                    pass
            if (
                not isinstance(error, RuntimeActivationCancelled)
                and activate
                and previous is not None
                and _live_runtime_state() is None
            ):
                # The pointer still describes the previous healthy deployment.
                # A subsequent ensure uses it, rather than the failed candidate.
                old_command, old_environment = previous
                for key in _LAUNCH_ENVIRONMENT:
                    environment.pop(key, None)
                environment.update(old_environment)
                recovery_id = uuid.uuid4().hex
                environment["WATCHER_RUNTIME_LAUNCH_ID"] = recovery_id
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
                while True:
                    recovered = _candidate_live_state()
                    if recovered is not None:
                        recovered_status = _request_json(
                            recovered.control_url, "/daemon/status"
                        )
                        if (
                            recovered_status.get("runtime", {}).get("launch_id")
                            != recovery_id
                        ):
                            raise RuntimeError(
                                "Rollback identity mismatch; inspect runtime.log"
                            )
                        break
                    if restored.poll() is not None:
                        raise RuntimeError(
                            "Activation failed and previous Runtime could not recover; see runtime.log"
                        )
                    time.sleep(0.05)
            raise
