"""Installer coordination must exclude both managers and direct Daemon starts."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from watcherobot.runtime import installation


def test_guard_holds_all_locks_until_installer_releases(tmp_path, monkeypatch):
    events = []

    @contextmanager
    def operation(root=None, **kwargs):
        events.append(("lock", root))
        yield
        events.append(("unlock", root))

    @contextmanager
    def lifetime(path):
        events.append(("lifetime", path))
        yield
        events.append(("released", path))

    monkeypatch.setattr(installation, "operation_lock", operation)
    monkeypatch.setattr(installation, "RuntimeInstanceLock", lifetime)
    monkeypatch.setattr(installation, "default_runtime_instance_root", lambda: tmp_path)
    monkeypatch.setattr(installation, "_stop_and_wait", lambda: events.append("stop"))
    owner = Mock()
    owner.is_running.return_value = True

    def release(_):
        assert (tmp_path / "ready").is_file()
        assert not any(e[0] == "unlock" for e in events if isinstance(e, tuple))
        (tmp_path / "release").touch()

    monkeypatch.setattr(installation.time, "sleep", release)
    installation.hold_installation(owner, tmp_path)
    assert events[0] == ("lock", None)
    assert events[1] == "stop"
    assert events[-1] == ("unlock", None)
    assert (tmp_path / "done").is_file()


def test_stop_failure_never_marks_installation_ready(tmp_path, monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(installation, "operation_lock", lambda **_: nullcontext())
    monkeypatch.setattr(
        installation, "_stop_and_wait", Mock(side_effect=RuntimeError("busy"))
    )
    with pytest.raises(RuntimeError, match="busy"):
        installation.hold_installation(Mock(), tmp_path)
    assert not (tmp_path / "ready").exists()


def test_dead_installer_releases_without_waiting(tmp_path, monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr(installation, "operation_lock", lambda *_, **__: nullcontext())
    monkeypatch.setattr(installation, "RuntimeInstanceLock", lambda _: nullcontext())
    monkeypatch.setattr(installation, "_stop_and_wait", lambda: None)
    monkeypatch.setattr(installation, "default_runtime_instance_root", lambda: tmp_path)
    owner = Mock()
    owner.is_running.return_value = False
    monkeypatch.setattr(
        installation.time, "sleep", Mock(side_effect=AssertionError("must release"))
    )
    installation.hold_installation(owner, tmp_path)
    assert (tmp_path / "done").is_file()


def test_real_guard_excludes_managers_and_daemons(tmp_path, monkeypatch):
    import os
    import time
    from pathlib import Path
    from watcherobot.runtime.daemon.instance import (
        RuntimeAlreadyRunningError,
        RuntimeInstanceLock,
    )
    from watcherobot.runtime.repository import operation_lock

    root = tmp_path / "instance"
    handshake = tmp_path / "handshake"
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(root))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2] / "src"))
    try:
        installation.begin_installation(os.getpid(), handshake)
        with pytest.raises(RuntimeAlreadyRunningError):
            with operation_lock(timeout=0):
                pytest.fail("manager entered installation")
        with pytest.raises(RuntimeAlreadyRunningError):
            with RuntimeInstanceLock(root / "runtime.lock"):
                pytest.fail("Daemon entered installation")
        with pytest.raises(RuntimeAlreadyRunningError):
            with operation_lock(root / "bundles", timeout=0):
                pytest.fail("bundle publisher entered installation")
    finally:
        handshake.mkdir(exist_ok=True)
        (handshake / "release").touch()
        deadline = time.monotonic() + 10
        while not (handshake / "done").is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
    assert (handshake / "done").is_file()
    with operation_lock(timeout=0):
        with RuntimeInstanceLock(root / "runtime.lock"):
            pass
