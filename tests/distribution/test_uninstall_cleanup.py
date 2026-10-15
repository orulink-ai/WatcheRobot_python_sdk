"""Only disposable uninstall roots are collected, on both platform layouts."""

import json
import stat
from pathlib import Path

import pytest

from watcherobot.distribution.install import cleanup_uninstalled_applications


@pytest.fixture(autouse=True)
def isolated_references(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(
        "watcherobot.runtime.cleanup.collect_runtime_garbage", lambda: None
    )


def residue(store, name, python_path=".venv/bin/python"):
    root = store / "trash" / name
    for rel in ["source/app.py", python_path, "install.json"]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("retained")
    return root


@pytest.mark.parametrize(
    "python_path", [".venv/bin/python", ".venv/Scripts/python.exe"]
)
def test_cleanup_deletes_only_old_uninstall_roots(tmp_path, python_path):
    store = tmp_path / "中文 store"
    old = residue(store, "a" * 32 + "-uninstall", python_path)
    keep = [
        residue(store, name)
        for name in ["b" * 32 + "-previous", "c" * 8, "not-owned-uninstall"]
    ]
    active = store / "apps/com.example.active"
    active.mkdir(parents=True)
    result = cleanup_uninstalled_applications(store_root=store)
    assert result == {"removed_count": 1, "failed_count": 0}
    assert not old.exists() and active.exists() and all(p.exists() for p in keep)


def test_cleanup_skips_root_links_and_never_follows_child_links(tmp_path):
    store = tmp_path / "store"
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep").write_text("safe")
    old = residue(store, "a" * 32 + "-uninstall")
    try:
        (old / "linked").symlink_to(external, target_is_directory=True)
        (store / "trash" / ("b" * 32 + "-uninstall")).symlink_to(
            external, target_is_directory=True
        )
    except OSError:
        pytest.skip("symbolic links require platform privileges")
    assert cleanup_uninstalled_applications(store_root=store)["removed_count"] == 1
    assert (external / "keep").read_text() == "safe"


def test_locked_residue_is_reported_and_other_residues_still_cleaned(
    tmp_path, monkeypatch
):
    store = tmp_path / "store"
    locked = residue(store, "a" * 32 + "-uninstall")
    other = residue(store, "b" * 32 + "-uninstall")
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if path == locked / "source/app.py":
            raise PermissionError("busy")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail)
    assert cleanup_uninstalled_applications(store_root=store) == {
        "removed_count": 1,
        "failed_count": 1,
    }
    assert locked.exists() and not other.exists()
    monkeypatch.setattr(Path, "unlink", original)
    assert cleanup_uninstalled_applications(store_root=store) == {
        "removed_count": 1,
        "failed_count": 0,
    }


def test_readonly_files_can_be_removed(tmp_path):
    store = tmp_path / "store"
    old = residue(store, "a" * 32 + "-uninstall")
    (old / "source/app.py").chmod(stat.S_IREAD)
    assert cleanup_uninstalled_applications(store_root=store)["removed_count"] == 1


def test_cleanup_cli_reports_counts_without_internal_paths(tmp_path, capsys):
    from watcherobot.distribution.cli import main

    old = residue(tmp_path, "a" * 32 + "-uninstall")
    assert main(["app", "cleanup", "--store-root", str(tmp_path), "--jsonl"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["data"] == {"removed_count": 1, "failed_count": 0}
    assert not old.exists() and str(tmp_path) not in str(events)


def test_cleanup_does_not_run_shared_runtime_gc(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError(
            "uninstall residue cleanup must not collect shared runtimes"
        )

    monkeypatch.setattr(
        "watcherobot.runtime.cleanup.collect_runtime_garbage", forbidden
    )
    residue(tmp_path, "a" * 32 + "-uninstall")
    assert cleanup_uninstalled_applications(store_root=tmp_path)["removed_count"] == 1


def test_cleanup_rejects_reparse_root(tmp_path, monkeypatch):
    from types import SimpleNamespace

    root = residue(tmp_path, "a" * 32 + "-uninstall")
    original = Path.lstat

    def reparse(path, *args, **kwargs):
        if path == root:
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse)
    assert cleanup_uninstalled_applications(store_root=tmp_path)["removed_count"] == 0
    assert (root / "source/app.py").exists()


def test_cleanup_rejects_reparse_trash_parent_on_older_python(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from watcherobot.distribution.install import ApplicationInstallError

    root = residue(tmp_path, "a" * 32 + "-uninstall")
    original = Path.lstat

    def reparse(path, *args, **kwargs):
        if path == tmp_path / "trash":
            return SimpleNamespace(
                st_mode=stat.S_IFDIR,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ApplicationInstallError):
        cleanup_uninstalled_applications(store_root=tmp_path)
    assert (root / "source/app.py").exists()


def test_windows_readonly_attribute_retry_does_not_touch_link_targets(
    tmp_path, monkeypatch
):
    from watcherobot.distribution.install import _remove_owned_entry

    file = tmp_path / "readonly.py"
    file.write_text("test")
    file.chmod(stat.S_IREAD)
    original = Path.unlink

    def windows_unlink(path):
        if not path.stat().st_mode & stat.S_IWRITE:
            raise PermissionError("read-only attribute")
        original(path)

    monkeypatch.setattr(Path, "unlink", windows_unlink)
    _remove_owned_entry(file, directory=False, link=False, windows=True)
    assert not file.exists()
    file.write_text("test")
    file.chmod(stat.S_IREAD)
    with pytest.raises(PermissionError):
        _remove_owned_entry(file, directory=False, link=True, windows=True)
    assert not file.stat().st_mode & stat.S_IWRITE


@pytest.mark.parametrize("operation", ["install", "uninstall"])
def test_store_writes_retry_historical_cleanup_without_deleting_current_app(
    tmp_path, operation
):
    from tests.distribution.test_install import (
        COMMIT,
        SPACE_ID,
        FakeEnvironmentRunner,
        FakeHub,
        _write_runtime,
        _write_source,
    )
    from watcherobot.distribution.install import (
        install_application,
        uninstall_application,
    )

    store = tmp_path / "store"
    old = residue(store, "a" * 32 + "-uninstall")
    rollback = residue(store, "b" * 32 + "-previous")
    if operation == "install":
        source, runtime = tmp_path / "source", tmp_path / "runtime"
        _write_source(source)
        _write_runtime(runtime)
        result = install_application(
            provider="gitee",
            repo_id=SPACE_ID,
            commit=COMMIT,
            store_root=store,
            runtime_root=runtime,
            hub=FakeHub(source),
            environment_runner=FakeEnvironmentRunner(),
        )
        assert result.application_root.exists()
    else:
        app = store / "apps/com.example.current"
        app.mkdir(parents=True)
        (app / "file").write_text("active")
        uninstall_application(store_root=store, application_id="com.example.current")
        assert not app.exists()
    assert not old.exists() and rollback.exists()


def test_cleanup_cli_failure_is_an_error_with_counts(tmp_path, monkeypatch, capsys):
    from watcherobot.distribution.cli import main

    old = residue(tmp_path, "a" * 32 + "-uninstall")

    def busy(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(
        "watcherobot.distribution.install._delete_application_tree", busy
    )
    assert main(["app", "cleanup", "--store-root", str(tmp_path), "--jsonl"]) == 5
    event = json.loads(capsys.readouterr().out)
    assert event["type"] == "error" and event["ok"] is False
    assert event["details"] == {"removed_count": 0, "failed_count": 1}
    assert old.exists() and str(tmp_path) not in str(event)
