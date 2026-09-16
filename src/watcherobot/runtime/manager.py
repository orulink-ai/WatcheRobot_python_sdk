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


_ROLLBACK_READINESS_TIMEOUT_SECONDS = 60.0


def _check_activation_cancelled() -> None:
    marker = os.environ.get("WATCHER_RUNTIME_CANCEL_FILE")
    if marker and Path(marker).exists():
        raise RuntimeActivationCancelled("Runtime activation cancelled by launcher")


def describe_command(
    command: list[str], environment: dict[str, str] | None = None
) -> dict[str, str]:
    """Exercise the candidate service imports before interrupting anything."""
    if (
        getattr(sys, "frozen", False)
        and command
        and Path(command[0]).resolve() == Path(sys.executable).resolve()
    ):
        # Avoid a nested onefile extraction, but still exercise the lazily loaded
        # service dependency tree before an existing Runtime is interrupted.
        from .daemon.runtime import DaemonRuntime as _DaemonRuntime
        from .identity import runtime_identity

        del _DaemonRuntime
        return runtime_identity()
    options: dict[str, Any] = {}
    if os.name == "nt":
        options["creationflags"] = CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [*command, "--check-runtime"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=True,
            env=environment,
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


def stop_shared_runtime(state_root: Path | None = None) -> None:
    """Stop the currently verified instance, regardless of its original client."""
    with operation_lock(timeout=120):
        _stop_and_wait(state_root)


def _stop_and_wait(state_root: Path | None = None) -> None:
    """Wait for both the Daemon and frozen bootloader to release their files.

    Capture process objects before shutdown so PID reuse cannot select a new
    process. No process is forcibly terminated from untrusted status metadata.
    """
    import psutil
    from urllib.parse import urlsplit
    from watcherobot.cli import CliError, _live_runtime_state, stop_runtime
    from .process_shutdown import capture_runtime, force_stop

    try:
        live = _live_runtime_state(state_root)
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
        stopped = stop_runtime(state_root)
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


def _candidate_live_state(state_root: Path | None = None) -> RuntimeProcessState | None:
    """Poll an already spawned candidate without mistaking bind for readiness.

    Pre-launch discovery remains strict. This probe never authorizes a process:
    the caller still verifies launch_id and build_id before committing activation.
    Transient probe errors keep waiting while the candidate remains alive.
    """
    from watcherobot.cli import CliError, _live_runtime_state

    try:
        return _live_runtime_state(state_root)
    except CliError:
        return None


class _SpawnedProcessTree:
    """Retain verified process identities even after a short-lived parent exits."""

    def __init__(self, process: subprocess.Popen[Any]) -> None:
        import psutil

        self._root = psutil.Process(process.pid)
        self._processes: dict[tuple[int, float], psutil.Process] = {}
        self.refresh()

    def refresh(self) -> None:
        import psutil

        candidates = [self._root, *self._processes.values()]
        visited: set[tuple[int, float]] = set()
        for candidate in candidates:
            try:
                identity = (candidate.pid, candidate.create_time())
            except psutil.Error:
                continue
            if identity in visited:
                continue
            visited.add(identity)
            self._processes[identity] = candidate
            try:
                descendants = candidate.children(recursive=True)
            except psutil.Error:
                continue
            for descendant in descendants:
                try:
                    descendant_identity = (
                        descendant.pid,
                        descendant.create_time(),
                    )
                except psutil.Error:
                    continue
                self._processes[descendant_identity] = descendant

    def terminate(self) -> None:
        """Stop only identities observed as descendants of this launch."""
        import psutil

        self.refresh()
        processes = list(reversed(tuple(self._processes.values())))
        for candidate in processes:
            try:
                candidate.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(processes, timeout=10)
        for candidate in alive:
            try:
                candidate.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=5)
        if alive:
            raise RuntimeError("Runtime launcher process tree could not be stopped")


def _terminate_process_tree(
    process: subprocess.Popen[Any], tree: _SpawnedProcessTree | None = None
) -> None:
    """Best-effort termination for a launcher and children created by this call."""
    import psutil

    try:
        (tree or _SpawnedProcessTree(process)).terminate()
    except psutil.NoSuchProcess:
        pass


def _with_runtime_state_root(command: list[str], state_root: Path) -> list[str]:
    """Return a launcher command pinned to the manager's discovery root."""

    normalized: list[str] = []
    index = 0
    while index < len(command):
        value = command[index]
        if value == "--state-root":
            if index + 1 >= len(command):
                raise ValueError("Runtime launcher --state-root requires a value")
            index += 2
            continue
        if value.startswith("--state-root="):
            index += 1
            continue
        normalized.append(value)
        index += 1
    normalized.extend(("--state-root", str(state_root)))
    return normalized


@cleanup_after
def ensure_command(
    command: list[str],
    *,
    activate: bool = False,
    force: bool = False,
    state_root: Path | None = None,
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

    resolved_state_root = (
        Path(state_root).resolve()
        if state_root is not None
        else default_runtime_state_root()
    )
    root = default_runtime_instance_root()
    pointer = root / "current-launcher.json"
    with operation_lock(timeout=120):
        try:
            live = _live_runtime_state(resolved_state_root)
        except CliError:
            if not activate:
                raise
            live = None
        if live is not None:
            if not activate:
                return True

        target_identity = describe_command(command) if activate else None
        try:
            previous = read_launcher(pointer) if pointer.is_file() else None
        except (OSError, ValueError):
            if not activate:
                raise
            previous = None

        selected = _with_runtime_state_root(command, resolved_state_root)
        launch_environment = {
            key: os.environ[key] for key in _LAUNCH_ENVIRONMENT if key in os.environ
        }
        if not activate and pointer.is_file():
            saved_command, launch_environment = read_launcher(pointer)
            selected = _with_runtime_state_root(saved_command, resolved_state_root)

        environment = dict(os.environ)
        for key in _LAUNCH_ENVIRONMENT:
            environment.pop(key, None)
        environment.update(launch_environment)
        environment["WATCHER_RUNTIME_STATE_ROOT"] = str(resolved_state_root)
        if not activate:
            target_identity = describe_command(selected, environment)

        log_root = resolved_state_root
        log_root.mkdir(parents=True, exist_ok=True)
        options: dict[str, Any] = {}
        options.update(background_process_options())

        process = None
        process_tree = None
        stopped_existing = False
        try:
            if live is not None:
                status = _request_json(live.control_url, "/daemon/status")
                if (
                    not force
                    and target_identity is not None
                    and status.get("runtime", {}).get("sdk_version")
                    == target_identity["sdk_version"]
                ):
                    return True
                # Shutdown closes admission and drains the managed Application.
                _stop_and_wait(resolved_state_root)
                stopped_existing = True

            # A detached frozen child must own its extraction directory. Otherwise
            # the short-lived manager can remove DLLs still used by the Daemon.
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            launch_id = uuid.uuid4().hex
            environment["WATCHER_RUNTIME_LAUNCH_ID"] = launch_id
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
                process_tree = _SpawnedProcessTree(process)
            # Cold extraction and file scanning have no predictable duration.
            # Fail on process exit or identity errors, not elapsed startup time.
            while True:
                _check_activation_cancelled()
                process_tree.refresh()
                live = _candidate_live_state(resolved_state_root)
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
            if process is not None:
                _terminate_process_tree(process, process_tree)
            if (
                not isinstance(error, RuntimeActivationCancelled)
                and activate
                and stopped_existing
                and previous is not None
                and _live_runtime_state(resolved_state_root) is None
            ):
                # The pointer still describes the previous healthy deployment.
                # A subsequent ensure uses it, rather than the failed candidate.
                old_command, old_environment = previous
                old_command = _with_runtime_state_root(
                    old_command, resolved_state_root
                )
                for key in _LAUNCH_ENVIRONMENT:
                    environment.pop(key, None)
                environment.update(old_environment)
                environment["WATCHER_RUNTIME_STATE_ROOT"] = str(
                    resolved_state_root
                )
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
                restored_tree = _SpawnedProcessTree(restored)
                deadline = time.monotonic() + _ROLLBACK_READINESS_TIMEOUT_SECONDS
                try:
                    while True:
                        _check_activation_cancelled()
                        restored_tree.refresh()
                        recovered = _candidate_live_state(resolved_state_root)
                        if recovered is not None:
                            try:
                                recovered_status = _request_json(
                                    recovered.control_url, "/daemon/status"
                                )
                            except CliError:
                                recovered_status = None
                            if recovered_status is not None:
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
                        if time.monotonic() >= deadline:
                            raise RuntimeError(
                                "Activation failed and previous Runtime recovery timed out; see runtime.log"
                            )
                        time.sleep(0.05)
                except Exception:
                    _terminate_process_tree(restored, restored_tree)
                    raise
            raise
