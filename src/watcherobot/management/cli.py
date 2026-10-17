"""Bounded local JSONL bridge; no Daemon is required to query or repair state."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from .service import Manager


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def dispatch_request(
    request: dict[str, Any], emit: Callable[[dict[str, Any]], None] = emit
) -> int:
    try:
        if not isinstance(request, dict):
            raise TypeError("SDK management request must be an object")
        manager = Manager(request, emit=emit)
        from .bootstrap import forward_management

        forwarded = forward_management(manager, output=emit)
        if forwarded is not None:
            return forwarded
        result: dict[str, Any]
        action = request.get("action", "status")
        if action == "begin_activity":
            result = manager.begin_activity(request["kind"], request["owner_pid"])
        elif action == "end_activity":
            from .activity import ActivityRegistry

            ActivityRegistry().end(request["token"])
            result = {"released": True}
        elif action == "control":
            result = manager.control(
                request["method"], request["path"], request.get("body")
            )
        elif action == "status":
            result = manager.status()
        elif action == "check_update":
            result = manager.check_update()
        elif action in {"update", "change_version"}:
            result = manager.update(
                request.get("identity"), request.get("confirmation")
            )
        elif action == "recover":
            result = manager.recover()
        elif action == "rollback":
            result = manager.rollback()
        elif action == "repair":
            result = manager.repair()
        elif action == "restore_default_runtime":
            result = manager.restore_default()
        elif action == "cancel":
            manager.store.request_cancel(request["task_id"])
            result = manager.status()
        elif action == "cleanup":
            result = manager.cleanup()
        elif action == "resolve":
            if manager.store.needs_recovery() and not manager.store.operation_running():
                manager.recover()
            manager._require_supported()
            entry = request.get("entry", "daemon")
            if entry not in {"daemon", "distribution"}:
                raise ValueError("Unknown SDK executable entry")
            result = {
                "command": manager.command(entry=entry)
                + (
                    ["--ensure-shared", *manager.daemon_args()]
                    if entry == "daemon"
                    else []
                ),
            }
        else:
            raise ValueError("Unknown SDK management operation")
        emit({"type": "result", "ok": True, "data": result})
        return 0
    except Exception as exc:  # noqa: BLE001 -- public CLI serializes all operation failures
        from .errors import describe_error

        emit(
            {
                "type": "result",
                "ok": False,
                "error": describe_error(
                    exc, action=str(request.get("action", "status"))
                )
                if isinstance(request, dict)
                else describe_error(exc, action="status"),
            }
        )
        return 1


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("SDK management request exceeds limit")
        request = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 -- input boundary
        from .errors import describe_error

        emit(
            {
                "type": "result",
                "ok": False,
                "error": describe_error(exc, action="status"),
            }
        )
        return 1
    return dispatch_request(request)
