"""Process identity checks and bounded shutdown for the shared Runtime."""

import os
from pathlib import Path

import psutil


def capture_runtime(pid: int, control_port: int) -> list[psutil.Process]:
    """Capture only a Runtime that owns the endpoint, before asking it to stop.

    Retained Process objects detect PID reuse in terminate/kill. Descendants are
    captured before the parent exits, including the managed Application.
    """
    process = psutil.Process(pid)
    command = process.cmdline()
    executable = process.exe()
    module = any(
        command[index : index + 2] == ["-m", "watcherobot.runtime.daemon"]
        for index in range(len(command) - 1)
    )
    frozen = Path(executable).name.lower() in {"watcher-runtime.exe", "watcher-runtime"}
    if not (module or frozen):
        raise RuntimeError("Runtime process identity cannot be verified")
    if not any(
        item.status == psutil.CONN_LISTEN and item.laddr and item.laddr.port == control_port
        for item in process.net_connections(kind="tcp")
    ):
        raise RuntimeError("Runtime PID does not own the control listener")
    processes = [*reversed(process.children(recursive=True)), process]
    parent = process.parent()
    if frozen and parent is not None and parent.exe() == executable:
        processes.append(parent)
    return processes


def force_stop(processes: list[psutil.Process]) -> None:
    """Terminate captured processes, then kill stragglers and await file release."""
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=5)
    if alive:
        raise RuntimeError("Previous Runtime still holds files; operation cancelled")


def stop_legacy_listener() -> bool:
    """Migrate a legacy default listener only after OS-level verification."""
    from .daemon.instance import (
        default_runtime_instance_root,
        system_runtime_instance_root,
        runtime_instance_id,
    )
    from watcherobot.cli import CliError, _request_json

    if default_runtime_instance_root() != system_runtime_instance_root():
        return False
    port = int(os.environ.get("WATCHER_RUNTIME_CONTROL_PORT", "8767"))
    if port == 0:
        return False
    owners = {
        item.pid
        for item in psutil.net_connections(kind="tcp")
        if item.status == psutil.CONN_LISTEN
        and item.laddr
        and item.laddr.port == port
        and item.pid is not None
    }
    if len(owners) != 1:
        return False
    try:
        status = _request_json(
            f"http://127.0.0.1:{port}", "/daemon/status", timeout=0.5
        )
    except CliError:
        status = {}
    runtime = status.get("runtime", {}) if isinstance(status, dict) else {}
    if isinstance(runtime, dict) and runtime.get("instance_group") == "isolated":
        raise RuntimeError("Control port belongs to an isolated Runtime")
    if isinstance(runtime, dict) and runtime.get("instance_id") not in {
        None,
        runtime_instance_id(system_runtime_instance_root()),
    }:
        raise RuntimeError("Control port belongs to another Runtime instance")
    processes = capture_runtime(owners.pop(), port)
    if processes[-1].username() != psutil.Process().username():
        raise RuntimeError("Runtime listener belongs to another user")
    force_stop(processes)
    return True
