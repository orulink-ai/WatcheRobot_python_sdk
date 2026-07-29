from __future__ import annotations

import json
from pathlib import Path

from watcherobot.runtime.daemon.logging import DaemonLogService


def test_daemon_logs_are_bounded_queryable_and_persisted(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "logs" / "daemon.jsonl"
    service = DaemonLogService(
        log_path=log_path,
        max_entries=2,
        clock_ms=lambda: 1_000,
        initial_id=0,
    )

    first = service.record("runtime starting")
    second = service.record("runtime ready")
    third = service.record("device connected")

    assert first["id"] == 1
    assert second["id"] == 2
    assert third["id"] == 3
    assert service.recent() == [second, third]
    assert service.recent(after_id=2) == [third]
    assert all(
        isinstance(event["timestamp_ms"], int)
        for event in service.recent()
    )

    persisted = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert persisted == [first, second, third]
