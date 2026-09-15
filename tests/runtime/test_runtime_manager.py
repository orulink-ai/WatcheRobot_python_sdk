from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from watcherobot import cli
from watcherobot.runtime import manager


def test_frozen_self_validation_does_not_spawn_another_extractor(monkeypatch, tmp_path):
    import sys
    from watcherobot.runtime import identity

    binary = tmp_path / "watcher-runtime.exe"
    binary.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(binary))
    expected = {"sdk_version": "0.1.9", "build_id": "bundle:test"}
    monkeypatch.setattr(identity, "runtime_identity", lambda: expected)
    spawned = Mock(side_effect=AssertionError("must not extract self again"))
    monkeypatch.setattr(manager.subprocess, "run", spawned)
    assert manager.describe_command([str(binary), "--control-port", "8767"]) == expected
    spawned.assert_not_called()


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


def test_sdk_application_start_delegates_to_shared_manager(tmp_path, monkeypatch):
    ready = SimpleNamespace(pid=123, control_url="http://unused")
    states = iter([None, ready])
    monkeypatch.setattr(cli, "_live_runtime_state", lambda *args: next(states))
    ensure = Mock(return_value=False)
    monkeypatch.setattr(manager, "ensure_command", ensure)
    state, reused = cli.ensure_runtime(state_root=tmp_path, ephemeral_ports=True)
    assert state is ready
    assert reused is False
    command = ensure.call_args.args[0]
    assert "watcherobot.runtime.daemon" in command
    assert command[command.index("--state-root") + 1] == str(tmp_path.resolve())
    assert command[command.index("--control-port") + 1] == "0"
    assert Path(command[0]).name.lower() != "pythonw.exe"


def test_candidate_probe_waits_for_slow_status_without_accepting_identity(monkeypatch):
    probe = Mock(side_effect=cli.CliError("status temporarily unavailable"))
    monkeypatch.setattr(cli, "_live_runtime_state", probe)
    assert manager._candidate_live_state() is None
    ready = SimpleNamespace(pid=123, control_url="http://unused")
    probe.side_effect = None
    probe.return_value = ready
    assert manager._candidate_live_state() is ready


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
    from watcherobot.runtime import process_shutdown

    lifecycle.return_value = True
    process = Mock()
    monkeypatch.setattr(psutil, "wait_procs", lambda *args, **kwargs: ([], [process]))
    forced = Mock()
    monkeypatch.setattr(process_shutdown, "force_stop", forced)
    manager.stop_shared_runtime()
    forced.assert_called_once_with([process])
    lifecycle.assert_called_once()


def test_shutdown_success_is_independent_of_original_launcher(lifecycle):
    lifecycle.return_value = True
    manager.stop_shared_runtime()
    lifecycle.assert_called_once()


def test_failed_readiness_reaps_only_the_candidate_tree(tmp_path, monkeypatch):
    import psutil

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_live_runtime_state", lambda: None)
    monkeypatch.setattr(
        manager,
        "describe_command",
        lambda _: {
            "sdk_version": "0.1.9",
            "build_id": "test",
        },
    )
    clock = iter([0, 0, 31])
    monkeypatch.setattr(manager.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(manager, "operation_lock", lambda **_: nullcontext())
    candidate = Mock(pid=12345)
    candidate.poll.return_value = None
    spawn = Mock(return_value=candidate)
    monkeypatch.setattr(manager.subprocess, "Popen", spawn)
    parent, child = Mock(), Mock()
    parent.children.return_value = [child]
    lookup = Mock(return_value=parent)
    monkeypatch.setattr(psutil, "Process", lookup)
    monkeypatch.setattr(psutil, "wait_procs", lambda *_, **__: ([], []))

    with pytest.raises(RuntimeError, match="readiness timed out"):
        manager.ensure_command(["missing-test-candidate"], activate=True)

    lookup.assert_called_once_with(candidate.pid)
    parent.terminate.assert_called_once()
    child.terminate.assert_called_once()
    assert spawn.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert not (tmp_path / "instance/current-launcher.json").exists()


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
