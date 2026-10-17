"""Stable public failure codes; clients present SDK-owned recovery suggestions."""

from __future__ import annotations

import errno
import subprocess
from typing import Any

import httpx


def describe_error(error: Exception, *, action: str) -> dict[str, Any]:
    from .activity import ActivityBusy
    from .state import ManagerBusy, ManagerCancelled

    causes: list[BaseException] = []
    cause: BaseException | None = error
    while cause is not None and all(cause is not previous for previous in causes):
        causes.append(cause)
        cause = cause.__cause__
    code, recovery, retryable = "sdk_operation_failed", "retry", True
    for item in reversed(causes):
        if isinstance(item, OSError) and item.errno in {
            errno.ENOSPC,
            getattr(errno, "EDQUOT", errno.ENOSPC),
        }:
            code, recovery = "sdk_disk_full", "free_disk_space"
        elif isinstance(item, PermissionError):
            code, recovery = "sdk_permission_denied", "check_permissions"
        elif isinstance(
            item, (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)
        ):
            code, recovery = (
                ("daemon_unavailable", "restore_default_runtime")
                if action == "control"
                else ("sdk_network_unavailable", "retry")
            )
        elif isinstance(item, ManagerBusy):
            code, recovery = "sdk_operation_active", "wait"
        elif isinstance(item, ActivityBusy):
            code, recovery = "sdk_activity_active", "wait"
        elif isinstance(item, ManagerCancelled):
            code, recovery = "sdk_cancelled", "retry"
        elif isinstance(item, subprocess.TimeoutExpired):
            code, recovery = "sdk_runtime_timeout", "repair"
        elif isinstance(item, (TypeError, KeyError, ValueError)):
            code, recovery, retryable = (
                "sdk_validation_failed",
                "review_diagnostics",
                False,
            )
        else:
            continue
        break
    messages = {
        "sdk_disk_full": "There is not enough disk space. Free space and retry; the previous SDK is retained.",
        "sdk_permission_denied": "The SDK could not access its files. Check folder permissions and retry.",
        "sdk_network_unavailable": "The SDK update source is unavailable. Check your Internet connection and retry; the current SDK is retained.",
        "daemon_unavailable": "The local Daemon is unavailable. Check SDK status and restore the selected runtime if needed.",
        "sdk_operation_active": "Another SDK operation is running. Wait for it to finish.",
        "sdk_activity_active": "An application, maintenance or Desktop update operation prevents this SDK change. Wait and retry.",
        "sdk_cancelled": "The SDK update was cancelled. The previous SDK is retained.",
        "sdk_runtime_timeout": "The SDK runtime did not respond in time. Check diagnostics and repair the runtime if needed.",
    }
    return {
        "code": code,
        "message": messages.get(
            code,
            str(error) or "SDK operation failed; check diagnostics before retrying.",
        ),
        "retryable": retryable,
        "suggested_action": recovery,
        "details": {"exception": type(error).__name__},
    }
