from __future__ import annotations

import json
from pathlib import Path

import pytest

from watcherobot import cli


def api():
    from watcherobot import project

    return project


@pytest.mark.parametrize("kind", ["application", "firmware", "combined"])
def test_three_kinds(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "中文 project"
    result = api().init_project(target, kind=kind)
    data = result.to_dict()
    assert data["kind"] == kind
    assert data["project_root"] == str(target.resolve())
    assert set(data) == {
        "kind",
        "project_root",
        "application_directory",
        "firmware_directory",
        "firmware_metadata",
        "firmware_build_directory",
    }
    assert bool(data["application_directory"]) == (kind != "firmware")
    assert bool(data["firmware_directory"]) == (kind != "application")
    if kind != "firmware":
        app = Path(data["application_directory"])
        assert (app / "app.py").is_file()
        assert not json.loads((app / "app.json").read_text("utf-8")).get(
            "requires_firmware"
        )
    else:
        assert not list(target.rglob("app.json"))
    if kind != "application":
        assert set(data["firmware_metadata"]) == {
            "id",
            "name",
            "version",
            "description",
        }
    assert api().inspect_project(target) == result.to_dict()


@pytest.mark.parametrize(
    "first,second", [("application", "firmware"), ("firmware", "application")]
)
def test_add_member(tmp_path: Path, first: str, second: str) -> None:
    target = tmp_path / "project"
    api().init_project(target, kind=first)
    before = {
        p.relative_to(target): p.read_bytes()
        for p in target.rglob("*")
        if p.is_file() and p.name != "watcherobot.project.json"
    }
    assert api().add_project_member(target, kind=second).kind == "combined"
    for path, content in before.items():
        assert (target / path).read_bytes() == content
    with pytest.raises(ValueError):
        api().add_project_member(target, kind=second)


@pytest.mark.parametrize("existing", ["file", "directory"])
def test_no_overwrite(tmp_path: Path, existing: str) -> None:
    target = tmp_path / "project"
    target.write_text("keep") if existing == "file" else target.mkdir()
    with pytest.raises(ValueError):
        api().init_project(target, kind="firmware")
    assert (
        target.read_text() == "keep"
        if existing == "file"
        else not list(target.iterdir())
    )


def test_legacy_root_application(tmp_path: Path) -> None:
    from watcherobot.application.project import init_application_project

    target = tmp_path / "legacy"
    init_application_project(
        target,
        app_id="local.test",
        name="Legacy",
        author="Team",
        description="Test",
        supported_host_platforms=["windows", "macos"],
    )
    assert api().inspect_project(target)["application_directory"] == str(
        target.resolve()
    )
    assert api().add_project_member(target, kind="firmware").kind == "combined"
    assert (
        json.loads((target / "watcherobot.project.json").read_text())[
            "application_path"
        ]
        == "."
    )


@pytest.mark.parametrize(
    "path",
    ["../outside", "/outside", "C:/outside", "a/../../outside", "a\\..\\outside"],
)
def test_unsafe_member_paths(tmp_path: Path, path: str) -> None:
    target = tmp_path / "project"
    target.mkdir()
    (target / "watcherobot.project.json").write_text(
        json.dumps({"schema_version": 1, "kind": "firmware", "firmware_path": path})
    )
    with pytest.raises(ValueError):
        api().inspect_project(target)


def test_conflicting_add_leaves_manifest_untouched(tmp_path: Path) -> None:
    target = tmp_path / "project"
    api().init_project(target, kind="application")
    manifest = target / "watcherobot.project.json"
    before = manifest.read_bytes()
    (target / "firmware").mkdir()
    (target / "firmware" / "keep").write_text("keep")
    with pytest.raises(ValueError):
        api().add_project_member(target, kind="firmware")
    assert manifest.read_bytes() == before
    assert (target / "firmware" / "keep").read_text() == "keep"


def test_cli_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli, "ensure_runtime", lambda *a, **k: pytest.fail("Daemon must not start")
    )
    target = tmp_path / "cli project"
    assert cli.main(["project", "init", str(target), "--kind", "firmware"]) == 0
    capsys.readouterr()
    assert cli.main(["project", "inspect", str(target), "--jsonl"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "firmware"
    assert cli.main(["project", "add", "application", str(target)]) == 0
    capsys.readouterr()
    assert cli.main(["project", "add", "application", str(target)]) == 2


def test_inspect_public_json_dictionary(tmp_path: Path) -> None:
    target = tmp_path / "project"
    api().init_project(target, kind="firmware")
    assert isinstance(api().inspect_project(target), dict)


def test_add_rollback_on_manifest_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    target = tmp_path / "project"
    api().init_project(target, kind="application")
    before = (target / "watcherobot.project.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("simulated manifest replace failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="simulated"):
        api().add_project_member(target, kind="firmware")
    assert not (target / "firmware").exists()
    assert not (target / ".watcherobot-project.lock").exists()
    assert (target / "watcherobot.project.json").read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [("id", ""), ("version", 42), ("build_path", "../outside"), ("offset", "0x1000")],
)
def test_invalid_firmware_metadata(tmp_path: Path, field: str, value: object) -> None:
    target = tmp_path / "project"
    api().init_project(target, kind="firmware")
    manifest = target / "firmware" / "firmware.project.json"
    data = json.loads(manifest.read_text())
    data[field] = value
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        api().inspect_project(target)


def test_firmware_optional_build_path(tmp_path: Path) -> None:
    target = tmp_path / "project"
    api().init_project(target, kind="firmware")
    manifest = target / "firmware" / "firmware.project.json"
    data = json.loads(manifest.read_text())
    data["build_path"] = "build/debug"
    manifest.write_text(json.dumps(data))
    assert api().inspect_project(target)["firmware_build_directory"] is None
    (target / "firmware" / "build" / "debug").mkdir(parents=True)
    assert api().inspect_project(target)["firmware_build_directory"] == str(
        (target / "firmware" / "build" / "debug").resolve()
    )
    assert "build_path" not in api().inspect_project(target)["firmware_metadata"]


def test_symlink_project_and_member_rejected(tmp_path: Path) -> None:
    target, link = tmp_path / "project", tmp_path / "link"
    api().init_project(target, kind="firmware")
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation unavailable: {exc}")
    with pytest.raises(ValueError):
        api().inspect_project(link)
    with pytest.raises(ValueError):
        api().init_project(link / "child", kind="application")
    assert not (target / "child").exists()
    app_link = target / "application"
    app_link.symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError):
        api().add_project_member(target, kind="application")
    assert app_link.is_symlink()


def test_windows_junction_escape_rejected(tmp_path: Path) -> None:
    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("Windows junction-specific test")
    target = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    api().init_project(target, kind="firmware")
    junction = target / "application"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        pytest.skip("Junction creation unavailable")
    try:
        with pytest.raises(ValueError):
            api().add_project_member(target, kind="application")
        assert not list(outside.iterdir())
        with pytest.raises(ValueError):
            api().init_project(junction / "child", kind="application")
    finally:
        junction.rmdir()


def test_default_build_directory_and_invalid_root(tmp_path: Path) -> None:
    target = tmp_path / "project"
    api().init_project(target, kind="firmware")
    assert api().inspect_project(target)["firmware_build_directory"] is None
    build = target / "firmware" / "build"
    build.mkdir()
    assert api().inspect_project(target)["firmware_build_directory"] == str(
        build.resolve()
    )
    with pytest.raises(ValueError):
        api().inspect_project(tmp_path)


def test_init_failure_cleans_new_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "project"
    original = Path.rename

    def fail(self: Path, destination: Path):
        if Path(destination).parent == target:
            raise OSError("simulated move failure")
        return original(self, destination)

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError, match="simulated"):
        api().init_project(target, kind="combined")
    assert not target.exists()
    assert not list(tmp_path.iterdir())


def test_junction_guard_works_without_new_pathlib_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("Windows reparse point test")
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        pytest.skip("Junction creation unavailable")
    monkeypatch.setattr(Path, "is_junction", lambda self: False, raising=False)
    try:
        with pytest.raises(ValueError):
            api().init_project(junction / "new-project", kind="firmware")
        assert not list(outside.iterdir())
    finally:
        junction.rmdir()


def test_schema_version_and_long_project_name(tmp_path: Path) -> None:
    from watcherobot.firmware.contract import firmware_id

    target = tmp_path / ("a" * 100)
    data = api().init_project(target, kind="firmware").to_dict()
    assert firmware_id(data["firmware_metadata"]["id"])
    manifest = target / "watcherobot.project.json"
    assert json.loads(manifest.read_text())["schema_version"] == 1


@pytest.mark.parametrize("schema", [None, 2, True, "1"])
def test_unsupported_project_schema(tmp_path: Path, schema: object) -> None:
    target = tmp_path / "project"
    api().init_project(target, kind="firmware")
    manifest = target / "watcherobot.project.json"
    data = json.loads(manifest.read_text())
    data["schema_version"] = schema
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        api().inspect_project(target)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "../bad"),
        ("id", "a" * 65),
        ("version", "nonsense"),
        ("version", "1.2.3rc1"),
    ],
)
def test_firmware_identity_contract(tmp_path: Path, field: str, value: str) -> None:
    target = tmp_path / "project"
    api().init_project(target, kind="firmware")
    manifest = target / "firmware" / "firmware.project.json"
    data = json.loads(manifest.read_text())
    data[field] = value
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        api().inspect_project(target)
