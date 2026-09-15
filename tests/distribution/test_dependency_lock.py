import json
from pathlib import Path

import pytest
from watcherobot.distribution.dependency_lock import read_dependency_lock


@pytest.mark.parametrize(
    "dependencies",
    [
        ["watcherobot>=0.1.7"],
        ["watcherobot==0.1.9"],
        ["watcherobot==0.1.7", "httpx>=0.27"],
        ["watcherobot==0.1.7", "watcherobot==0.1.7"],
    ],
)
def test_invalid_or_incompatible_lock_is_rejected(
    tmp_path: Path, dependencies: list[str]
) -> None:
    (tmp_path / "app.lock.json").write_text(
        json.dumps({"schema_version": 1, "dependencies": dependencies}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        read_dependency_lock(tmp_path, "==0.1.7")


def test_application_sdk_pin_is_independent_of_daemon(tmp_path: Path) -> None:
    pins = ["watcherobot==0.1.7", "httpx==0.27.2"]
    (tmp_path / "app.lock.json").write_text(
        json.dumps({"schema_version": 1, "dependencies": pins}), encoding="utf-8"
    )
    assert read_dependency_lock(tmp_path, ">=0.1.7,<0.2") == tuple(pins)
