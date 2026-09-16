import json
from pathlib import Path

import pytest
from watcherobot.distribution import dependency_lock
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


def test_declared_dependencies_must_be_satisfied_by_lock(tmp_path: Path) -> None:
    pins = ["watcherobot==0.1.7", "httpx==0.27.2"]
    (tmp_path / "app.lock.json").write_text(
        json.dumps({"schema_version": 1, "dependencies": pins}), encoding="utf-8"
    )

    assert read_dependency_lock(
        tmp_path, ">=0.1.7,<0.2", ("httpx>=0.27,<0.28",)
    ) == tuple(pins)
    with pytest.raises(ValueError, match="covered"):
        read_dependency_lock(tmp_path, ">=0.1.7,<0.2", ("rich>=13",))
    with pytest.raises(ValueError, match="does not satisfy"):
        read_dependency_lock(tmp_path, ">=0.1.7,<0.2", ("httpx>=0.28",))


@pytest.mark.parametrize(
    "requirement", ["httpx[socks]==0.27.2", "httpx[socks]>=0.27"]
)
def test_portable_lock_rejects_extras(tmp_path: Path, requirement: str) -> None:
    (tmp_path / "app.lock.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dependencies": ["watcherobot==0.1.7", requirement],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="extras"):
        read_dependency_lock(tmp_path, "==0.1.7")


@pytest.mark.parametrize("declared", [("rich>=13",), ("httpx>=0.28",)])
def test_lock_generator_preserves_previous_file_on_manifest_mismatch(
    tmp_path: Path, monkeypatch, declared: tuple[str, ...]
) -> None:
    from watcherobot.runtime.daemon.application import manifest as manifest_module

    target = tmp_path / "app.lock.json"
    previous = b'{"previous": true}\n'
    target.write_bytes(previous)
    fake_manifest = type(
        "Manifest",
        (),
        {"requires_watcherobot": ">=0.1,<0.2", "dependencies": declared},
    )()
    monkeypatch.setattr(
        manifest_module.ApplicationManifest, "load", lambda _: fake_manifest
    )
    records = [
        type(
            "Dist",
            (),
            {"metadata": {"Name": "watcherobot"}, "version": "0.1.9"},
        )(),
        type(
            "Dist",
            (),
            {"metadata": {"Name": "httpx"}, "version": "0.27.2"},
        )(),
    ]
    monkeypatch.setattr("importlib.metadata.distributions", lambda: records)
    monkeypatch.setattr("sys.argv", ["dependency-lock", str(tmp_path)])

    with pytest.raises(ValueError):
        dependency_lock.main()

    assert target.read_bytes() == previous
