from pathlib import Path

import pytest

from watcherobot.management.deployment import activate, recover
from watcherobot.management.state import ManagerStore


def descriptor(version):
    return {
        "version": version,
        "build_id": "source:" + version,
        "contracts": {"state": 1},
        "kind": "bundled",
    }


class Runtime:
    def __init__(self):
        self.current = descriptor("old")
        self.fail = False

    def status(self):
        return {
            "runtime": {"build_id": self.current["build_id"]},
            "application": {"state": "not_running"},
        }

    def start(self, target):
        if self.fail:
            raise RuntimeError("candidate failed")
        self.current = target


def test_switch_commits_only_after_verified_start(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    store = ManagerStore(tmp_path / "manager")
    runtime = Runtime()
    activate(store, descriptor("new"), descriptor("old"), runtime)
    assert store.snapshot()["selection"]["version"] == "new"
    assert store.snapshot()["previous"]["version"] == "old"
    assert store.snapshot()["transaction"] is None


def test_failed_activation_retains_previous_selection(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    store = ManagerStore(tmp_path / "manager")
    runtime = Runtime()
    runtime.fail = True
    with pytest.raises(RuntimeError):
        activate(store, descriptor("new"), descriptor("old"), runtime)
    assert store.snapshot()["selection"] is None
    assert store.snapshot()["transaction"] is None


def test_recovery_does_not_kill_later_external_takeover(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    store = ManagerStore(tmp_path / "manager")
    state = store.snapshot()
    state["transaction"] = {"before": descriptor("old"), "target": descriptor("new")}
    store.save(state)
    runtime = Runtime()
    runtime.current = descriptor("external")
    assert recover(store, runtime) == "external_takeover"
    assert runtime.current["version"] == "external"
    assert store.snapshot()["transaction"] is None


def test_candidate_identity_drift_is_rejected_before_stopping_live_daemon(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from watcherobot import cli
    from watcherobot.runtime import manager

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(
        cli,
        "_live_runtime_state",
        lambda root: SimpleNamespace(control_url="http://127.0.0.1:1"),
    )
    monkeypatch.setattr(
        manager,
        "describe_command",
        lambda command: {"sdk_version": "0.1.13", "build_id": "source:changed"},
    )
    monkeypatch.setattr(
        manager,
        "_stop_and_wait",
        lambda root: pytest.fail("must preserve the old Daemon"),
    )
    with pytest.raises(ValueError, match="verified SDK identity"):
        manager._ensure_command_locked(
            ["python", "-m", "watcherobot.runtime.daemon"],
            activate=True,
            force=True,
            expected_identity={"sdk_version": "0.1.13", "build_id": "source:verified"},
        )


def test_disk_full_when_committing_keeps_durable_transaction_for_recovery(
    tmp_path, monkeypatch
):
    import errno

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    store = ManagerStore(tmp_path / "manager")
    runtime = Runtime()
    original_save = store.save
    failed = False

    def save(state):
        nonlocal failed
        if not failed and state.get("selection") == descriptor("new"):
            failed = True
            raise OSError(errno.ENOSPC, "injected full disk on activation commit")
        original_save(state)

    monkeypatch.setattr(store, "save", save)
    with pytest.raises(OSError, match="full disk"):
        activate(store, descriptor("new"), descriptor("old"), runtime)
    assert runtime.current["version"] == "new"
    assert store.snapshot()["selection"] is None
    assert store.snapshot()["transaction"]["target"]["version"] == "new"
    assert recover(store, runtime) == "completed"
    assert store.snapshot()["selection"]["version"] == "new"
    assert store.snapshot()["transaction"] is None


@pytest.mark.parametrize(
    "phase,expected,outcome",
    [
        ("before_launch", "old", "restored"),
        ("after_launch", "new", "completed"),
        ("external_takeover", "external", "external_takeover"),
    ],
)
def test_hard_exit_during_switch_releases_locks_and_recovers_without_reclaiming(
    tmp_path, monkeypatch, phase, expected, outcome
):
    """Real manager process/locks, with a file-backed Daemon model and no device ports."""
    import json
    import subprocess
    import sys
    import time

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    root, ready, live = (
        tmp_path / "manager",
        tmp_path / "ready",
        tmp_path / "model-live.json",
    )
    live.write_text(json.dumps(descriptor("old")))
    code = r"""
import json,sys,time
from pathlib import Path
from watcherobot.management.deployment import activate
from watcherobot.management.state import ManagerStore,atomic_json
root,ready,live,phase=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]
def descriptor(version):
    return {"version":version,"build_id":"source:"+version,"contracts":{"state":1},"kind":"bundled"}
class Runtime:
    def status(self):
        return {"runtime":{"build_id":json.loads(live.read_bytes())["build_id"]},"application":{"state":"not_running"}}
    def start(self,target):
        if phase!="before_launch":atomic_json(live,target)
        ready.write_text("critical section")
        time.sleep(60)
store=ManagerStore(root)
with store.operation("update") as task:
    store.transition(task,"switching")
    activate(store,descriptor("new"),descriptor("old"),Runtime())
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(root), str(ready), str(live), phase],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while (
            not ready.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert ready.exists()
        process.kill()
        process.wait(timeout=5)
        store = ManagerStore(root)
        assert store.needs_recovery()
        assert not store.operation_running()
        if phase == "external_takeover":
            live.write_text(json.dumps(descriptor(expected)))
        runtime = Runtime()
        runtime.current = json.loads(live.read_bytes())
        assert runtime.current["version"] == expected
        starts = []
        monkeypatch.setattr(runtime, "start", lambda target: starts.append(target))
        with store.operation("recover", recovery=True):
            assert recover(store, runtime) == outcome
        assert not starts
        assert runtime.current["version"] == expected
        assert not store.needs_recovery()
        assert store.snapshot()["selection"] == (
            descriptor("new") if outcome == "completed" else None
        )
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()
