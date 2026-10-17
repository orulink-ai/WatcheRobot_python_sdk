from __future__ import annotations

from pathlib import Path

import pytest

from watcherobot.management.state import ManagerBusy, ManagerStore


def test_only_one_operation_and_candidate(tmp_path: Path) -> None:
    first = ManagerStore(tmp_path)
    second = ManagerStore(tmp_path)
    with first.operation("update") as task:
        assert task.phase == "checking"
        first.transition(task, "downloading", downloaded_bytes=10, total_bytes=100)
        assert second.snapshot()["operation"]["progress"]["downloaded_bytes"] == 10
        with pytest.raises(ManagerBusy), second.operation("update"):
            pass
    assert first.snapshot()["operation"]["phase"] == "completed"


def test_crash_records_recovery_required_not_false_success(tmp_path: Path) -> None:
    store = ManagerStore(tmp_path)
    with pytest.raises(KeyboardInterrupt), store.operation("update") as task:
        store.transition(task, "switching")
        raise KeyboardInterrupt()
    assert store.snapshot()["operation"]["phase"] == "switching"
    assert store.needs_recovery()


def test_cancel_is_sdk_owned_and_operation_specific(tmp_path: Path) -> None:
    store = ManagerStore(tmp_path)
    with store.operation("update") as task:
        with pytest.raises(ValueError):
            store.request_cancel("different-task")
        store.request_cancel(task.id)
        assert store.cancelled(task)


def test_invalid_persistent_schema_is_rejected(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "state.json").write_text('{"schema_version":999}')
    with pytest.raises(ValueError):
        ManagerStore(tmp_path).snapshot()


def test_process_lock_is_released_after_hard_exit_and_state_requires_recovery(
    tmp_path, monkeypatch
):
    import subprocess
    import sys
    import time

    from watcherobot.management.service import Manager

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    root = tmp_path / "manager"
    ready = tmp_path / "ready"
    code = """
import sys,time
from pathlib import Path
from watcherobot.management.state import ManagerStore
store=ManagerStore(Path(sys.argv[1]))
with store.operation('update') as task:
    store.transition(task,'downloading')
    Path(sys.argv[2]).write_text(task.id)
    time.sleep(60)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(root), str(ready)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while (
            not ready.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert ready.exists()
        other = ManagerStore(root)
        assert other.operation_running()
        with pytest.raises(ManagerBusy), other.operation("update"):
            pass
        process.kill()
        process.wait(timeout=5)
        assert not other.operation_running()
        assert other.needs_recovery()
        manager = Manager(
            {
                "desktop": {
                    "manager": ">=1,<2",
                    "management": ">=1,<2",
                    "distribution": ">=1,<2",
                }
            },
            root=root,
            live=lambda: None,
        )
        assert manager.status()["actions"]["recover"]["enabled"]
        manager.recover()
        assert not other.needs_recovery()
        with other.operation("check_update"):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()


def test_oversized_state_is_rejected_before_replacing_last_valid_snapshot(tmp_path):
    store = ManagerStore(tmp_path)
    before = store.snapshot()
    store.save(before)
    state = store.snapshot()
    state["unexpected_large_payload"] = "x" * (1024 * 1024)
    with pytest.raises(ValueError, match="exceeds limit"):
        store.save(state)
    assert store.snapshot() == before


def test_atomic_write_disk_failure_preserves_last_state_and_removes_temporary_file(
    tmp_path, monkeypatch
):
    import errno

    from watcherobot.management import state as module

    store = ManagerStore(tmp_path)
    initial = store.snapshot()
    store.save(initial)
    before = store.path.read_bytes()
    monkeypatch.setattr(
        module.os,
        "replace",
        lambda *args: (_ for _ in ()).throw(
            OSError(errno.ENOSPC, "injected full disk")
        ),
    )
    with pytest.raises(OSError):
        store.save(store.snapshot())
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
