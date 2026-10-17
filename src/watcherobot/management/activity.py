"""SDK-owned operation leases for native jobs and short critical sections."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil
from filelock import FileLock, Timeout

from watcherobot.runtime.daemon.instance import default_runtime_instance_root

from .state import atomic_json


class ActivityBusy(RuntimeError):
    """A running operation prevents a safe runtime switch."""


class ActivityRegistry:
    def __init__(self, root: Path | None = None):
        self.root = root or default_runtime_instance_root() / "sdk-activities"
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock = FileLock(str(self.root / "activity.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise ActivityBusy(
                "SDK runtime is switching; retry after completion"
            ) from exc
        try:
            yield
        finally:
            lock.release()

    def active(self) -> list[dict]:
        active = []
        for path in self.root.glob("*.json"):
            item = json.loads(path.read_bytes())
            try:
                process = psutil.Process(item["pid"])
                if process.create_time() == item["created_at"] and process.is_running():
                    active.append(item)
                else:
                    path.unlink()
            except psutil.NoSuchProcess:
                path.unlink(missing_ok=True)
        return active

    def begin(self, kind: str, owner_pid: int) -> str:
        if kind not in {"maintenance", "desktop_ota", "application_install"}:
            raise ValueError("Unknown SDK activity type")
        owner = psutil.Process(owner_pid)
        if owner.username() != psutil.Process().username():
            raise ValueError("SDK activity must belong to the current user")
        with self._lock():
            token = uuid.uuid4().hex
            atomic_json(
                self.root / (token + ".json"),
                {
                    "id": token,
                    "kind": kind,
                    "pid": owner_pid,
                    "created_at": owner.create_time(),
                },
            )
        return token

    def end(self, token: str) -> None:
        if len(token) != 32 or any(x not in "0123456789abcdef" for x in token):
            raise ValueError("Invalid SDK activity token")
        with self._lock():
            path = self.root / (token + ".json")
            if not path.is_file():
                raise ValueError("SDK activity is no longer registered")
            path.unlink()

    @contextmanager
    def switch(self) -> Iterator[None]:
        with self._lock():
            if self.active():
                raise ActivityBusy(
                    "A maintenance or desktop update task is still running"
                )
            yield
