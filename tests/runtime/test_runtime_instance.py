from __future__ import annotations

from pathlib import Path

import pytest

from watcherobot.runtime.daemon.instance import (
    RuntimeAlreadyRunningError,
    RuntimeInstanceLock,
    RuntimeProcessState,
    RuntimeStateStore,
)


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
