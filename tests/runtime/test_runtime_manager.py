from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

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


def test_candidate_validation_loads_service_dependencies(monkeypatch):
    completed = SimpleNamespace(
        stdout=json.dumps({"sdk_version": "0.1.9", "build_id": "test"})
    )
    run = Mock(return_value=completed)
    monkeypatch.setattr(manager.subprocess, "run", run)

    assert manager.describe_command(["candidate-runtime"]) == {
        "sdk_version": "0.1.9",
        "build_id": "test",
    }
    assert run.call_args.args[0] == ["candidate-runtime", "--check-runtime"]


def test_candidate_validation_uses_selected_environment(monkeypatch):
    completed = SimpleNamespace(
        stdout=json.dumps({"sdk_version": "0.1.9", "build_id": "test"})
    )
    run = Mock(return_value=completed)
    monkeypatch.setattr(manager.subprocess, "run", run)

    manager.describe_command(["candidate-runtime"], {"SDK_CHANNEL": "saved"})

    assert run.call_args.kwargs["env"] == {"SDK_CHANNEL": "saved"}


def test_live_runtime_reuse_ignores_corrupt_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance")
    )
    pointer = tmp_path / "instance/current-launcher.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("broken", encoding="utf-8")
    monkeypatch.setattr(manager, "operation_lock", lambda **_: nullcontext())
    monkeypatch.setattr(
        cli,
        "_live_runtime_state",
        lambda *_: SimpleNamespace(control_url="http://unused"),
    )
    validate = Mock(side_effect=AssertionError("must reuse before validation"))
    monkeypatch.setattr(manager, "describe_command", validate)

    assert manager.ensure_command(["unused"]) is True
    validate.assert_not_called()


def test_cold_start_validates_saved_launcher_and_environment(tmp_path, monkeypatch):
    import psutil

    monkeypatch.setenv(
        "WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance")
    )
    pointer = tmp_path / "instance/current-launcher.json"
    pointer.parent.mkdir(parents=True)
    saved_runtime = tmp_path / "saved-runtime.exe"
    saved_runtime.touch()
    pointer.write_text(
        json.dumps(
            {
                "command": [str(saved_runtime)],
                "environment": {"PYTHONUTF8": "1"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(manager, "operation_lock", lambda **_: nullcontext())
    monkeypatch.setattr(cli, "_live_runtime_state", lambda *_: None)
    validate = Mock(
        return_value={"sdk_version": "0.1.9", "build_id": "saved-build"}
    )
    monkeypatch.setattr(manager, "describe_command", validate)
    ready = SimpleNamespace(control_url="http://unused")
    monkeypatch.setattr(manager, "_candidate_live_state", Mock(return_value=ready))
    candidate = Mock(pid=12345)
    candidate.poll.return_value = None
    spawn = Mock(return_value=candidate)
    monkeypatch.setattr(manager.subprocess, "Popen", spawn)
    parent = Mock(pid=candidate.pid)
    parent.create_time.return_value = 1.0
    parent.children.return_value = []
    monkeypatch.setattr(psutil, "Process", Mock(return_value=parent))
    monkeypatch.setattr(
        cli,
        "_request_json",
        lambda *_: {
            "runtime": {
                "build_id": "saved-build",
                "launch_id": spawn.call_args.kwargs["env"][
                    "WATCHER_RUNTIME_LAUNCH_ID"
                ],
            }
        },
    )
    monkeypatch.setattr(manager, "save_launcher", Mock())

    assert manager.ensure_command(["unused-runtime"]) is False
    validated_command, validated_environment = validate.call_args.args
    assert validated_command[0] == str(saved_runtime)
    assert validated_environment["PYTHONUTF8"] == "1"
    assert spawn.call_args.args[0][0] == str(saved_runtime)


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
        lambda *_: SimpleNamespace(
            control_url="http://unused:8767",
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


def test_candidate_probe_uses_explicit_state_root(monkeypatch, tmp_path):
    probe = Mock(return_value=None)
    monkeypatch.setattr(cli, "_live_runtime_state", probe)

    assert manager._candidate_live_state(tmp_path) is None

    probe.assert_called_once_with(tmp_path)


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
    monkeypatch.setattr(cli, "_live_runtime_state", lambda *_: None)
    monkeypatch.setattr(
        manager,
        "describe_command",
        lambda _: {
            "sdk_version": "0.1.9",
            "build_id": "test",
        },
    )
    monkeypatch.setattr(
        manager, "_candidate_live_state",
        Mock(side_effect=RuntimeError("candidate probe failed")),
    )
    monkeypatch.setattr(manager, "operation_lock", lambda **_: nullcontext())
    candidate = Mock(pid=12345)
    candidate.poll.return_value = None
    spawn = Mock(return_value=candidate)
    monkeypatch.setattr(manager.subprocess, "Popen", spawn)
    parent, child = Mock(), Mock()
    parent.pid, child.pid = 12345, 12346
    parent.create_time.return_value, child.create_time.return_value = 1.0, 2.0
    parent.children.return_value = [child]
    child.children.return_value = []
    lookup = Mock(return_value=parent)
    monkeypatch.setattr(psutil, "Process", lookup)
    monkeypatch.setattr(psutil, "wait_procs", lambda *_, **__: ([], []))

    with pytest.raises(RuntimeError, match="candidate probe failed"):
        manager.ensure_command(["missing-test-candidate"], activate=True)

    lookup.assert_called_once_with(candidate.pid)
    parent.terminate.assert_called_once()
    child.terminate.assert_called_once()
    assert spawn.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert not (tmp_path / "instance/current-launcher.json").exists()


def test_process_tree_reaps_observed_child_after_parent_exit(monkeypatch):
    import psutil

    process = Mock(pid=12345)
    parent, child = Mock(), Mock()
    parent.pid, child.pid = 12345, 12346
    parent.create_time.return_value, child.create_time.return_value = 1.0, 2.0
    parent.children.side_effect = [[child], psutil.NoSuchProcess(parent.pid)]
    child.children.return_value = []
    monkeypatch.setattr(psutil, "Process", Mock(return_value=parent))
    monkeypatch.setattr(psutil, "wait_procs", lambda *_, **__: ([], []))

    tree = manager._SpawnedProcessTree(process)
    manager._terminate_process_tree(process, tree)

    child.terminate.assert_called_once()


def test_process_tree_discovers_grandchild_after_parent_exit(monkeypatch):
    import psutil

    process = Mock(pid=12345)
    parent, child, grandchild = Mock(), Mock(), Mock()
    parent.pid, child.pid, grandchild.pid = 12345, 12346, 12347
    parent.create_time.return_value = 1.0
    child.create_time.return_value = 2.0
    grandchild.create_time.return_value = 3.0
    parent.children.side_effect = [
        [child],
        psutil.NoSuchProcess(parent.pid),
        psutil.NoSuchProcess(parent.pid),
    ]
    child.children.side_effect = [[], [grandchild], [grandchild], [grandchild]]
    grandchild.children.return_value = []
    monkeypatch.setattr(psutil, "Process", Mock(return_value=parent))
    monkeypatch.setattr(psutil, "wait_procs", lambda *_, **__: ([], []))

    tree = manager._SpawnedProcessTree(process)
    tree.refresh()
    manager._terminate_process_tree(process, tree)

    child.terminate.assert_called_once()
    grandchild.terminate.assert_called_once()


def test_launcher_command_replaces_existing_state_root(tmp_path):
    state_root = (tmp_path / "state").resolve()

    assert manager._with_runtime_state_root(
        ["runtime", "--state-root", "old", "--control-port", "0"],
        state_root,
    ) == [
        "runtime",
        "--control-port",
        "0",
        "--state-root",
        str(state_root),
    ]


def test_launcher_command_rejects_state_root_without_value(tmp_path):
    with pytest.raises(ValueError, match="--state-root requires a value"):
        manager._with_runtime_state_root(
            ["runtime", "--state-root"],
            tmp_path.resolve(),
        )


def test_rollback_timeout_reaps_previous_runtime(tmp_path, monkeypatch):
    import psutil

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "state"))
    pointer = tmp_path / "instance/current-launcher.json"
    pointer.parent.mkdir(parents=True)
    old_runtime = tmp_path / "old-runtime.exe"
    old_runtime.touch()
    pointer.write_text(
        json.dumps({"command": [str(old_runtime)], "environment": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(manager, "operation_lock", lambda **_: nullcontext())
    monkeypatch.setattr(
        manager,
        "describe_command",
        lambda _: {"sdk_version": "0.2.0", "build_id": "new"},
    )
    running = SimpleNamespace(control_url="http://unused")
    monkeypatch.setattr(
        cli, "_live_runtime_state", Mock(side_effect=[running, None])
    )
    monkeypatch.setattr(
        cli,
        "_request_json",
        lambda *_: {"runtime": {"sdk_version": "0.1.9"}},
    )
    monkeypatch.setattr(manager, "_stop_and_wait", Mock())
    monkeypatch.setattr(
        manager,
        "_candidate_live_state",
        Mock(side_effect=[RuntimeError("failed candidate"), None, None]),
    )
    candidate = Mock(pid=1)
    restored = Mock(pid=2)
    candidate.poll.return_value = None
    restored.poll.return_value = None
    monkeypatch.setattr(
        manager.subprocess, "Popen", Mock(side_effect=[candidate, restored])
    )
    roots = []
    for pid in (candidate.pid, restored.pid):
        root = Mock(pid=pid)
        root.create_time.return_value = float(pid)
        root.children.return_value = []
        roots.append(root)
    monkeypatch.setattr(psutil, "Process", Mock(side_effect=roots))
    terminate = Mock()
    monkeypatch.setattr(manager, "_terminate_process_tree", terminate)
    elapsed = [0.0]
    monkeypatch.setattr(manager.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        manager.time, "sleep", lambda _: elapsed.__setitem__(0, elapsed[0] + 61.0)
    )

    with pytest.raises(RuntimeError, match="recovery timed out"):
        manager.ensure_command(["new-runtime"], activate=True)

    assert [item.args[0] for item in terminate.call_args_list] == [
        candidate,
        restored,
    ]


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


@pytest.mark.parametrize("cancel", [False, True])
def test_slow_candidate_has_no_readiness_deadline(tmp_path, monkeypatch, cancel):
    import psutil

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(manager, "operation_lock", lambda **_: nullcontext())
    monkeypatch.setattr(cli, "_live_runtime_state", lambda *_: None)
    monkeypatch.setattr(
        manager,
        "describe_command",
        lambda _: {"sdk_version": "0.1.9", "build_id": "test"},
    )
    elapsed = [0]
    monkeypatch.setattr(manager.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(
        manager.time,
        "sleep",
        lambda _: elapsed.__setitem__(0, elapsed[0] + 61),
    )
    ready = SimpleNamespace(control_url="http://unused")
    monkeypatch.setattr(
        manager, "_candidate_live_state", Mock(side_effect=[None, None, ready])
    )
    candidate = Mock(pid=12345)
    candidate.poll.return_value = None
    spawn = Mock(return_value=candidate)
    monkeypatch.setattr(manager.subprocess, "Popen", spawn)
    parent = Mock(pid=candidate.pid)
    parent.create_time.return_value = 1.0
    parent.children.return_value = []
    monkeypatch.setattr(psutil, "Process", Mock(return_value=parent))
    monkeypatch.setattr(
        cli,
        "_request_json",
        lambda *args: {
            "runtime": {
                "build_id": "test",
                "launch_id": spawn.call_args.kwargs["env"][
                    "WATCHER_RUNTIME_LAUNCH_ID"
                ],
            }
        },
    )
    monkeypatch.setattr(manager, "save_launcher", Mock())
    if cancel:
        marker = tmp_path / "cancel"
        marker.touch()
        monkeypatch.setenv("WATCHER_RUNTIME_CANCEL_FILE", str(marker))
        monkeypatch.setattr(psutil, "wait_procs", lambda *_, **__: ([], []))
        with pytest.raises(manager.RuntimeActivationCancelled):
            manager.ensure_command(["test-candidate"], activate=True)
        parent.terminate.assert_called_once()
        manager.save_launcher.assert_not_called()
        assert spawn.call_count == 1
        return
    assert manager.ensure_command(["test-candidate"], activate=True) is False
    assert elapsed[0] >= 122
    assert spawn.call_count == 1
