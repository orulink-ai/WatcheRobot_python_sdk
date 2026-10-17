"""Durable single-operation state owned by the SDK, independent of Daemon uptime."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

_TERMINAL = frozenset({"completed", "failed", "cancelled", "recovered"})
_PHASES = _TERMINAL | frozenset(
    {
        "checking",
        "downloading",
        "installing",
        "verifying",
        "waiting",
        "switching",
        "health_check",
        "recovering",
        "cleaning",
    }
)


class ManagerBusy(RuntimeError):
    """Another process owns the shared SDK transaction."""


class ManagerCancelled(RuntimeError):
    """The user cancelled at a safe checkpoint."""


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Task:
    id: str
    action: str
    phase: str = "checking"


class ManagerStore:
    def __init__(
        self, root: Path, emit: Callable[[dict[str, Any]], None] | None = None
    ):
        self.root = Path(root)
        if self.root.is_symlink() or (
            hasattr(self.root, "is_junction") and self.root.is_junction()
        ):
            raise ValueError("SDK management root must not be a link")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.json"
        self.emit = emit

    def snapshot(self) -> dict[str, Any]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {
                "schema_version": 1,
                "revision": 0,
                "selection": None,
                "previous": None,
                "candidate": None,
                "operation": None,
                "transaction": None,
            }
        if len(raw) > 1024 * 1024:
            raise ValueError("SDK state exceeds limit")
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
        ):
            raise ValueError("Unsupported SDK state schema; do not overwrite it")
        if type(value.get("revision")) is not int or value["revision"] < 0:
            raise ValueError("Invalid SDK state revision")
        return value

    def save(self, state: dict[str, Any]) -> None:
        candidate = {**state, "revision": state["revision"] + 1}
        if (
            len(
                json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode(
                    "utf-8"
                )
            )
            > 1024 * 1024
        ):
            raise ValueError("SDK state exceeds limit")
        atomic_json(self.path, candidate)
        state["revision"] = candidate["revision"]

    def needs_recovery(self) -> bool:
        state = self.snapshot()
        operation = state.get("operation")
        return bool(
            state.get("transaction")
            or (operation and operation.get("phase") not in _TERMINAL)
        )

    def operation_running(self) -> bool:
        """Distinguish a live transaction from an abandoned persisted phase."""
        lock = FileLock(str(self.root / "update.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return True
        lock.release()
        return False

    @contextmanager
    def operation(self, action: str, *, recovery: bool = False) -> Iterator[Task]:
        lock = FileLock(str(self.root / "update.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise ManagerBusy("Another SDK operation is already active") from exc
        try:
            if self.needs_recovery() and not recovery:
                raise ManagerBusy(
                    "An interrupted SDK operation must be recovered first"
                )
            task = Task(uuid.uuid4().hex, action)
            self.transition(task, "checking")
            try:
                yield task
            except ManagerCancelled:
                self.transition(task, "cancelled")
                raise
            except Exception as exc:
                from .errors import describe_error

                self.transition(
                    task,
                    "failed",
                    error=describe_error(exc, action=action),
                )
                raise
            else:
                if task.phase not in _TERMINAL:
                    self.transition(task, "completed")
        finally:
            lock.release()

    def transition(self, task: Task, phase: str, **details: Any) -> None:
        if phase not in _PHASES:
            raise ValueError("Unknown SDK task phase")
        task.phase = phase
        state = self.snapshot()
        state["operation"] = {
            "id": task.id,
            "action": task.action,
            "phase": phase,
            "updated_at": time.time(),
            "progress": details,
        }
        self.save(state)
        if self.emit is not None:
            self.emit(
                {
                    "type": "progress",
                    "task_id": task.id,
                    "revision": state["revision"],
                    "phase": phase,
                    **details,
                }
            )

    def request_cancel(self, task_id: str) -> None:
        operation = self.snapshot().get("operation")
        if (
            not operation
            or operation["id"] != task_id
            or operation["phase"] in _TERMINAL
        ):
            raise ValueError("The SDK task is no longer active")
        if operation["phase"] in {"switching", "health_check", "recovering"}:
            raise ValueError(
                "SDK activation must finish or recover before cancellation"
            )
        atomic_json(self.root / "cancel.json", {"task_id": task_id})

    def cancelled(self, task: Task) -> bool:
        try:
            return (
                json.loads((self.root / "cancel.json").read_bytes()).get("task_id")
                == task.id
            )
        except FileNotFoundError:
            return False

    def checkpoint(self, task: Task) -> None:
        if self.cancelled(task):
            raise ManagerCancelled("SDK update cancelled; previous version retained")
