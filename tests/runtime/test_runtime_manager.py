from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from watcherobot import cli
from watcherobot.runtime import manager


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setattr(manager, "operation_lock", lambda **kwargs: nullcontext())
    monkeypatch.setattr(
        manager,
        "describe_command",
        lambda command: {
            "sdk_version": "0.1.9",
            "build_id": "different-build",
        },
    )
    monkeypatch.setattr(
        cli,
        "_live_runtime_state",
        lambda: SimpleNamespace(
            control_url="http://unused",
            pid=99999999,
        ),
    )
    monkeypatch.setattr(
        cli,
        "_request_json",
        lambda *args, **kwargs: {
            "runtime": {"sdk_version": "0.1.9", "build_id": "original-build"},
            "application": {"state": "running"},
        },
    )
    stopped = Mock(return_value=False)
    monkeypatch.setattr(cli, "stop_runtime", stopped)
    return stopped


def test_same_version_reuses_running_application_despite_different_build(lifecycle):
    assert manager.ensure_command(["missing-python"], activate=True) is True
    lifecycle.assert_not_called()


def test_force_activation_stops_same_version_and_refuses_second_process(lifecycle):
    with pytest.raises(RuntimeError, match="has not exited"):
        manager.ensure_command(["missing-python"], activate=True, force=True)
    lifecycle.assert_called_once()


def test_version_change_stops_active_application(lifecycle, monkeypatch):
    monkeypatch.setattr(
        manager,
        "describe_command",
        lambda command: {
            "sdk_version": "0.2.0",
            "build_id": "new-build",
        },
    )
    with pytest.raises(RuntimeError, match="has not exited"):
        manager.ensure_command(["missing-python"], activate=True)
    lifecycle.assert_called_once()


def test_invalid_candidate_does_not_stop_old_runtime(lifecycle, monkeypatch):
    def invalid(command):
        raise ValueError("invalid candidate")

    monkeypatch.setattr(manager, "describe_command", invalid)
    with pytest.raises(ValueError, match="invalid candidate"):
        manager.ensure_command(["missing-python"], activate=True)
    lifecycle.assert_not_called()


def test_shutdown_waits_for_process_release(lifecycle, monkeypatch):
    import psutil

    lifecycle.return_value = True
    monkeypatch.setattr(psutil, "wait_procs", lambda *args, **kwargs: ([], [object()]))
    with pytest.raises(RuntimeError, match="still holds files"):
        manager.stop_shared_runtime()
    lifecycle.assert_called_once()


def test_shutdown_success_is_independent_of_original_launcher(lifecycle):
    lifecycle.return_value = True
    manager.stop_shared_runtime()
    lifecycle.assert_called_once()


@pytest.mark.parametrize(
    ("operation", "helper"),
    [
        ("start", "ensure_command"),
        ("activate", "ensure_command"),
        ("stop", "stop_shared_runtime"),
    ],
)
def test_cli_reports_lifecycle_failure_without_traceback(
    monkeypatch, capsys, operation, helper
):
    import json

    monkeypatch.setattr(
        manager, helper, Mock(side_effect=RuntimeError("Runtime still holds files"))
    )
    assert cli.main(["daemon", operation]) == 2
    assert json.loads(capsys.readouterr().err) == {"error": "Runtime still holds files"}
