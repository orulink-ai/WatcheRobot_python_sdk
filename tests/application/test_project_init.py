from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from watcherobot.application.project import (
    ApplicationProjectInitError,
    init_application_project,
)
from watcherobot.runtime.daemon.application.manifest import ApplicationManifest


def test_init_creates_one_publish_ready_application_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "nested" / "robot-demo"

    result = init_application_project(
        target,
        app_id="com.example.robot_demo",
        name="Robot Demo",
        author="Example Team",
        description="A generated WatcheRobot Application.",
        supported_host_platforms=["windows", "macos"],
        watcherobot_version="0.1.1a3",
    )

    assert result.to_dict() == {
        "directory": str(target.resolve()),
        "id": "com.example.robot_demo",
        "name": "Robot Demo",
        "version": "0.1.0",
        "requires_watcherobot": ">=0.1.1a3,<0.2",
        "supported_host_platforms": ["windows", "macos"],
        "files": [
            ".gitignore",
            "README.md",
            "app.json",
            "app.py",
            "icon.svg",
        ],
    }
    assert tuple(sorted(path.name for path in target.iterdir())) == result.files
    manifest_document = json.loads(
        target.joinpath("app.json").read_text(encoding="utf-8")
    )
    assert manifest_document == {
        "schema_version": 2,
        "id": "com.example.robot_demo",
        "name": "Robot Demo",
        "version": "0.1.0",
        "requires_watcherobot": ">=0.1.1a3,<0.2",
        "dependencies": [],
        "supported_host_platforms": ["windows", "macos"],
        "description": "A generated WatcheRobot Application.",
        "author": "Example Team",
        "icon": "icon.svg",
    }
    manifest = ApplicationManifest.load(
        target,
        watcherobot_version="0.1.1a3",
    )
    assert manifest.app_id == "com.example.robot_demo"
    compile(
        target.joinpath("app.py").read_text(encoding="utf-8"),
        str(target / "app.py"),
        "exec",
    )
    app_source = target.joinpath("app.py").read_text(encoding="utf-8")
    assert 'app.robot.behavior.play,\n            "happy"' in app_source
    assert "job.wait, 20.0" in app_source
    assert "DEMO_BEHAVIORS = (" in app_source
    assert "random.shuffle(behaviors)" in app_source
    assert "while True:" in app_source
    assert "app.robot.lights.play_effect" in app_source
    assert "Press Ctrl+C to stop the behavior showcase." in app_source
    assert "watcherobot app check" in target.joinpath("README.md").read_text(
        encoding="utf-8"
    )
    assert target.joinpath("icon.svg").read_text(encoding="utf-8").startswith(
        "<svg"
    )

    namespace = runpy.run_path(
        str(target / "app.py"),
        run_name="generated_application_test",
    )
    monkeypatch.setattr(namespace["random"], "shuffle", lambda _items: None)
    behaviors = namespace["_shuffled_demo_behaviors"]("smile")
    assert behaviors[0] != "smile"
    assert tuple(namespace["DEMO_BEHAVIORS"]) == (
        "smile",
        "shock",
        "sunglasses",
        "speechless",
        "concentration",
        "get",
        "query",
        "fondle_love",
    )
    assert set(behaviors) == set(namespace["DEMO_BEHAVIORS"])


@pytest.mark.parametrize("existing_kind", ["file", "directory"])
def test_init_never_overwrites_an_existing_target(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    target = tmp_path / "existing"
    if existing_kind == "file":
        target.write_text("keep me", encoding="utf-8")
    else:
        target.mkdir()
        target.joinpath("keep.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(ApplicationProjectInitError) as captured:
        init_application_project(
            target,
            app_id="com.example.demo",
            name="Demo",
            author="Example",
            description="Example Application",
            supported_host_platforms=["windows"],
            watcherobot_version="0.1.1a3",
        )

    assert str(captured.value) == f"Target already exists: {target.resolve()}"
    if existing_kind == "file":
        assert target.read_text(encoding="utf-8") == "keep me"
    else:
        assert [path.name for path in target.iterdir()] == ["keep.txt"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("app_id", "Invalid ID"),
        ("name", "  "),
        ("author", ""),
        ("description", "\t"),
    ],
)
def test_init_rejects_invalid_metadata_without_leaving_a_project(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    target = tmp_path / "invalid"
    values = {
        "app_id": "com.example.demo",
        "name": "Demo",
        "author": "Example",
        "description": "Example Application",
        "supported_host_platforms": ["windows"],
    }
    values[field] = value

    with pytest.raises(ApplicationProjectInitError):
        init_application_project(
            target,
            **values,
            watcherobot_version="0.1.1a3",
        )

    assert not target.exists()


@pytest.mark.parametrize(
    ("sdk_version", "expected"),
    [
        ("0.1.1a3", ">=0.1.1a3,<0.2"),
        ("0.9.4", ">=0.9.4,<0.10"),
        ("1.3.0", ">=1.3.0,<2"),
    ],
)
def test_init_derives_a_bounded_sdk_requirement(
    tmp_path: Path,
    sdk_version: str,
    expected: str,
) -> None:
    target = tmp_path / sdk_version

    result = init_application_project(
        target,
        app_id="com.example.demo",
        name="Demo",
        author="Example",
            description="Example Application",
            supported_host_platforms=["windows"],
        watcherobot_version=sdk_version,
    )

    assert result.requires_watcherobot == expected
