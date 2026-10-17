"""Persistent management transport; no sockets, business routing or device links."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, BinaryIO, TextIO

from .cli import dispatch_request
from .errors import describe_error

MAX_FRAME = 1024 * 1024


def serve(
    incoming: BinaryIO | None = None,
    outgoing: TextIO | None = None,
    *,
    dispatch: Callable[
        [dict[str, Any], Callable[[dict[str, Any]], None]], int
    ] = dispatch_request,
) -> int:
    incoming = incoming or sys.stdin.buffer
    outgoing = outgoing or sys.stdout
    output_lock = threading.Lock()
    capacity = threading.BoundedSemaphore(32)

    def emit(identifier: str, message: dict[str, Any]) -> None:
        frame = json.dumps({**message, "id": identifier}, ensure_ascii=False)
        if len(frame.encode("utf-8")) > MAX_FRAME:
            frame = json.dumps(
                {
                    "id": identifier,
                    "type": "result",
                    "ok": False,
                    "error": {
                        "code": "sdk_response_too_large",
                        "message": "SDK management response exceeds limit",
                    },
                }
            )
        with output_lock:
            outgoing.write(frame + "\n")
            outgoing.flush()

    def execute(identifier: str, request: dict[str, Any]) -> None:
        try:
            dispatch(request, lambda message: emit(identifier, message))
        except Exception as exc:  # noqa: BLE001 -- isolate one request from other clients
            emit(
                identifier,
                {
                    "type": "result",
                    "ok": False,
                    "error": describe_error(
                        exc, action=str(request.get("action", "status"))
                    ),
                },
            )
        finally:
            capacity.release()

    # Concurrent status/cancel requests remain responsive during a long update.
    # EOF stops admission; admitted transactions finish their safe checkpoints.
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="sdk-management") as pool:
        while raw := incoming.readline(MAX_FRAME + 1):
            identifier = ""
            try:
                if len(raw) > MAX_FRAME:
                    raise ValueError("SDK management frame exceeds limit")
                frame = json.loads(raw)
                identifier = frame.get("id", "") if isinstance(frame, dict) else ""
                if not isinstance(identifier, str) or not 1 <= len(identifier) <= 64:
                    identifier = ""
                    raise ValueError("SDK management request identity is invalid")
                request = frame.get("request")
                if not isinstance(request, dict):
                    raise TypeError("SDK management request must be an object")
                if not capacity.acquire(blocking=False):
                    emit(
                        identifier,
                        {
                            "type": "result",
                            "ok": False,
                            "error": {
                                "code": "sdk_operation_active",
                                "message": "SDK management is busy; retry later",
                                "retryable": True,
                                "suggested_action": "wait",
                            },
                        },
                    )
                    continue
                pool.submit(execute, identifier, request)
            except Exception as exc:  # noqa: BLE001 -- bounded framing boundary
                emit(
                    identifier,
                    {
                        "type": "result",
                        "ok": False,
                        "error": describe_error(exc, action="status"),
                    },
                )
                if len(raw) > MAX_FRAME:
                    return 1
    return 0
