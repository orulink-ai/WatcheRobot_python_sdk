"""Per-user Runtime process locking and state discovery."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO


IS_WINDOWS = os.name == "nt"


class RuntimeAlreadyRunningError(RuntimeError):
    """Raised when another process owns the current user's Runtime lock."""


@dataclass(frozen=True)
class RuntimeProcessState:
    pid: int
    control_url: str
    external_url: str
    started_at: float


class RuntimeStateStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / "runtime-state.json"

    def read(self) -> RuntimeProcessState | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return RuntimeProcessState(
                pid=int(payload["pid"]),
                control_url=str(payload["control_url"]),
                external_url=str(payload["external_url"]),
                started_at=float(payload["started_at"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def write(self, state: RuntimeProcessState) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def remove(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class RuntimeInstanceLock:
    """Hold one byte locked for the lifetime of the Runtime process."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _lock_file(handle)
        except OSError as exc:
            handle.close()
            raise RuntimeAlreadyRunningError(
                "another Runtime already owns this user session"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            _unlock_file(handle)
        finally:
            handle.close()

    def __enter__(self) -> "RuntimeInstanceLock":
        self.acquire()
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        self.release()


def default_runtime_state_root() -> Path:
    configured = os.environ.get("WATCHER_RUNTIME_STATE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if IS_WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data).resolve() / "WatcheRobot" / "runtime"
    return Path.home() / ".watcherobot" / "runtime"


def default_runtime_instance_root() -> Path:
    """Return the per-user coordination root shared by every Runtime launcher.

    Runtime data may live in a launcher-specific ``state_root``.  The process
    lock must not: otherwise Desktop and an SDK program can each acquire a
    different lock and race to become the user's Daemon.
    """
    configured = os.environ.get("WATCHER_RUNTIME_INSTANCE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if IS_WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            return Path(local_app_data).resolve() / "WatcheRobot" / "runtime-instance"
    return Path.home() / ".watcherobot" / "runtime-instance"


if os.name == "nt":
    import msvcrt

    def _lock_file(handle: BinaryIO) -> None:
        getattr(msvcrt, "locking")(
            handle.fileno(),
            getattr(msvcrt, "LK_NBLCK"),
            1,
        )

    def _unlock_file(handle: BinaryIO) -> None:
        getattr(msvcrt, "locking")(
            handle.fileno(),
            getattr(msvcrt, "LK_UNLCK"),
            1,
        )

else:
    import fcntl

    def _lock_file(handle: BinaryIO) -> None:
        getattr(fcntl, "flock")(
            handle.fileno(),
            getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"),
        )

    def _unlock_file(handle: BinaryIO) -> None:
        getattr(fcntl, "flock")(
            handle.fileno(),
            getattr(fcntl, "LOCK_UN"),
        )
