"""Local-user launch grants; HTTP callers cannot authorize filesystem access."""

from __future__ import annotations

import json
import uuid
from contextvars import ContextVar
from typing import Any

from .daemon.instance import default_runtime_instance_root

authorized_launch: ContextVar[dict[str, Any] | None] = ContextVar(
    "authorized_launch", default=None
)


def register_launch(payload: dict[str, Any]) -> dict[str, Any]:
    """Grant one exact selection through the current user's local filesystem."""
    ticket = str(uuid.uuid4())
    root = default_runtime_instance_root() / "launch-grants"
    root.mkdir(parents=True, exist_ok=True)
    (root / (ticket + ".json")).write_text(json.dumps(payload), encoding="utf-8")
    return {**payload, "local_registration": ticket}


def consume_launch(ticket: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Consume a single-use local grant, rejecting paths and mismatched requests."""
    if str(uuid.UUID(ticket)) != ticket:
        raise ValueError("Invalid local launch registration")
    path = default_runtime_instance_root() / "launch-grants" / (ticket + ".json")
    saved = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    if not isinstance(saved, dict):
        raise ValueError("Invalid local launch registration document")
    environment = saved.pop("environment", {})
    from .manager import _LAUNCH_ENVIRONMENT

    if not isinstance(environment, dict) or any(
        k not in _LAUNCH_ENVIRONMENT or not isinstance(v, str)
        for k, v in environment.items()
    ):
        raise ValueError("Invalid registered Application environment")
    if saved != payload:
        raise ValueError("Local launch registration does not match selection")
    return {**payload, "environment": environment}
