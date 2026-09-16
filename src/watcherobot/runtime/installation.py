"""Installer-scoped exclusion, released automatically when its owner exits."""

from __future__ import annotations

import time
import os
import subprocess
import sys
from pathlib import Path

import psutil

from .daemon.instance import RuntimeInstanceLock, default_runtime_instance_root
from .manager import _stop_and_wait
from .repository import operation_lock
from .background_process import background_process_options

_GUARD_START_TIMEOUT_SECONDS = 150.0
_GUARD_CANCEL_WAIT_SECONDS = 125.0


def _installation_cancelled(owner: psutil.Process, handshake: Path) -> bool:
    return not owner.is_running() or (handshake / "release").exists()


def hold_installation(owner: psutil.Process, handshake: Path) -> None:
    """Stop the shared instance and prevent launches until installation ends.

    The manager lock excludes SDK activation; the lifetime lock also excludes
    direct Daemon entrypoints. The bundle lock excludes copying installer files
    into the immutable repository while the installer replaces those files.
    """
    try:
        with operation_lock(timeout=120):
            if _installation_cancelled(owner, handshake):
                return
            _stop_and_wait()
            if _installation_cancelled(owner, handshake):
                return
            root = default_runtime_instance_root()
            with RuntimeInstanceLock(root / "runtime.lock"):
                if _installation_cancelled(owner, handshake):
                    return
                with operation_lock(root / "bundles", timeout=120):
                    if _installation_cancelled(owner, handshake):
                        return
                    (handshake / "ready").touch()
                    while not _installation_cancelled(owner, handshake):
                        time.sleep(0.1)
    finally:
        (handshake / "done").touch()


def guard_installer(pid: int, handshake: Path) -> None:
    """Capture owner creation time through psutil to avoid following reused PIDs."""
    owner = psutil.Process(pid)
    owner.create_time()
    hold_installation(owner, handshake)


def begin_installation(pid: int, handshake: Path) -> None:
    """Return only after a detached guard has stopped Runtime and acquired locks."""
    handshake.mkdir(parents=True, exist_ok=False)
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.extend(["-m", "watcherobot.runtime.daemon"])
    command.extend(["--guard-installation", str(pid), str(handshake)])
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    options = background_process_options()
    with (handshake / "guard.log").open("ab") as log:
        process = subprocess.Popen(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            **options,
        )
    deadline = time.monotonic() + _GUARD_START_TIMEOUT_SECONDS
    while not (handshake / "ready").is_file():
        if process.poll() is not None or time.monotonic() >= deadline:
            # The guard observes release even if it is still acquiring locks.
            (handshake / "release").touch()
            cancel_deadline = time.monotonic() + _GUARD_CANCEL_WAIT_SECONDS
            while (
                not (handshake / "done").is_file()
                and process.poll() is None
                and time.monotonic() < cancel_deadline
            ):
                time.sleep(0.1)
            raise RuntimeError("Installer could not acquire Runtime maintenance locks")
        time.sleep(0.1)
