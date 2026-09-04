from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from watcherobot.runtime.daemon import instance as instance_module
from watcherobot.runtime.daemon.instance import (
    default_runtime_instance_root,
    default_runtime_state_root,
    RuntimeAlreadyRunningError,
    RuntimeInstanceLock,
    RuntimeProcessState,
    RuntimeStateStore,
    system_runtime_instance_root,
    system_runtime_state_root,
)


def test_runtime_instance_root_is_independent_from_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "desktop-data"))
    monkeypatch.delenv("WATCHER_RUNTIME_INSTANCE_ROOT", raising=False)
    monkeypatch.setattr(instance_module, "IS_WINDOWS", True)

    assert default_runtime_instance_root() == (
        local_app_data / "WatcheRobot" / "runtime-instance"
    ).resolve()


def test_runtime_instance_root_can_be_isolated_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = tmp_path / "isolated-instance"
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(expected))

    assert default_runtime_instance_root() == expected.resolve()


def test_system_runtime_roots_ignore_isolation_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.setattr(instance_module, "IS_WINDOWS", True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "isolated"))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "private-state"))

    assert system_runtime_instance_root() == (
        local_app_data / "WatcheRobot" / "runtime-instance"
    ).resolve()
    assert system_runtime_state_root() == (
        local_app_data / "WatcheRobot" / "runtime"
    ).resolve()


def test_runtime_instance_root_ignores_windows_environment_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(instance_module, "IS_WINDOWS", False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "unexpected-windows-root"))
    monkeypatch.delenv("WATCHER_RUNTIME_INSTANCE_ROOT", raising=False)

    assert default_runtime_instance_root() == Path.home() / ".watcherobot" / "runtime-instance"


def test_runtime_state_root_ignores_windows_environment_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(instance_module, "IS_WINDOWS", False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "unexpected-windows-root"))
    monkeypatch.delenv("WATCHER_RUNTIME_STATE_ROOT", raising=False)

    assert default_runtime_state_root() == Path.home() / ".watcherobot" / "runtime"


def test_runtime_instance_lock_rejects_a_second_owner(tmp_path: Path) -> None:
    first = RuntimeInstanceLock(tmp_path / "runtime.lock")
    second = RuntimeInstanceLock(tmp_path / "runtime.lock")

    first.acquire()
    try:
        with pytest.raises(RuntimeAlreadyRunningError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_runtime_instance_lock_rejects_another_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "cross-process.lock"
    ready_path = tmp_path / "child-ready"
    script = """
import sys
from pathlib import Path
from watcherobot.runtime.daemon.instance import RuntimeInstanceLock

lock = RuntimeInstanceLock(Path(sys.argv[1]))
lock.acquire()
Path(sys.argv[2]).write_text("ready", encoding="utf-8")
sys.stdin.readline()
lock.release()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path), str(ready_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5.0
        while (
            not ready_path.exists()
            and child.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        if not ready_path.exists():
            assert child.stderr is not None
            pytest.fail(f"lock child did not become ready: {child.stderr.read()}")
        competing = RuntimeInstanceLock(lock_path)
        with pytest.raises(RuntimeAlreadyRunningError):
            competing.acquire()
    finally:
        if child.stdin is not None:
            child.stdin.write("stop\n")
            child.stdin.flush()
        child.wait(timeout=5)

    assert child.returncode == 0


def test_runtime_state_store_round_trips_and_removes_state(
    tmp_path: Path,
) -> None:
    store = RuntimeStateStore(tmp_path)
    state = RuntimeProcessState(
        pid=1234,
        control_url="http://127.0.0.1:4567",
        external_url="ws://127.0.0.1:8765",
        started_at=100.5,
    )

    store.write(state)

    assert store.read() == state
    store.remove()
    assert store.read() is None
