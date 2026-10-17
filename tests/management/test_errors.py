import errno

import httpx
import pytest

from watcherobot.management.errors import describe_error
from watcherobot.management.state import ManagerBusy


@pytest.mark.parametrize(
    "error,action,code,recovery",
    [
        (
            httpx.ConnectError("connection unavailable"),
            "check_update",
            "sdk_network_unavailable",
            "retry",
        ),
        (
            httpx.ConnectError("connection unavailable"),
            "control",
            "daemon_unavailable",
            "restore_default_runtime",
        ),
        (OSError(errno.ENOSPC, "full"), "update", "sdk_disk_full", "free_disk_space"),
        (
            PermissionError("denied"),
            "update",
            "sdk_permission_denied",
            "check_permissions",
        ),
        (ManagerBusy("active"), "update", "sdk_operation_active", "wait"),
    ],
)
def test_sdk_failures_have_stable_codes_and_recovery_actions(
    error, action, code, recovery
):
    result = describe_error(error, action=action)
    assert result["code"] == code
    assert result["suggested_action"] == recovery
    assert result["message"]
    assert isinstance(result["retryable"], bool)


def test_chained_disk_failure_preserves_actionable_cause():
    error = RuntimeError("environment installer failed")
    error.__cause__ = OSError(errno.ENOSPC, "full")
    assert describe_error(error, action="update")["code"] == "sdk_disk_full"
