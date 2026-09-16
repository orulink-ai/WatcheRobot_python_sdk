import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from watcherobot.runtime import cleanup


def version(root, name):
    path = root / (name * 64)
    path.mkdir(parents=True)
    (path / "python.exe").write_bytes(b"test")
    os.utime(path, (1, 1))
    return path


def test_publication_grace_starts_now_not_at_source_mtime(tmp_path, monkeypatch):
    from watcherobot.runtime.repository import prepare_bundle

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    source = tmp_path / "source"
    source.mkdir()
    (source / "runtime").write_bytes(b"bundle")
    os.utime(source, (1, 1))
    published = prepare_bundle(source)
    assert time.time() - published.stat().st_mtime < 10
    assert cleanup.collect_runtime_garbage()["deleted"] == []


def test_cleanup_retains_current_rollback_and_application_references(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    root = tmp_path / "bundles"
    old, current, rollback, referenced = [version(root, c) for c in "abcd"]
    for name, path in [("current", current), ("previous", rollback)]:
        (tmp_path / (name + "-launcher.json")).write_text(
            json.dumps({"command": [str(path / "python.exe")]})
        )
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyvenv.cfg").write_text("home = " + str(referenced))
    cleanup.register_reference(project)
    report = cleanup.collect_runtime_garbage()
    assert str(old) in report["deleted"]
    assert all(p.exists() for p in [current, rollback, referenced])


def test_application_and_external_environment_are_registered_atomically(
    tmp_path, monkeypatch
):
    instance = tmp_path / "instance"
    application = tmp_path / "application"
    environment = tmp_path / "external-venv"
    executable = environment / "bin" / "python"
    runtime = version(instance / "bundles", "a")
    application.mkdir()
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"python")
    environment.joinpath("pyvenv.cfg").write_text(
        "home = " + str(runtime), encoding="utf-8"
    )
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(instance))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    first_reference_written = threading.Event()
    continue_registration = threading.Event()
    original_register = cleanup._register_reference

    def register(path, *, store=False):
        original_register(path, store=store)
        if Path(path).resolve() == application.resolve():
            first_reference_written.set()
            assert continue_registration.wait(timeout=5)

    monkeypatch.setattr(cleanup, "_register_reference", register)
    registration = threading.Thread(
        target=cleanup.register_application_reference,
        args=(application, executable),
    )
    registration.start()
    assert first_reference_written.wait(timeout=5)

    report = cleanup.collect_runtime_garbage()
    continue_registration.set()
    registration.join(timeout=5)

    assert not registration.is_alive()
    assert report["deleted"] == []
    assert report["error"]
    assert runtime.exists()
    assert len(list(instance.joinpath("runtime-references").glob("*.json"))) == 2


def test_automatic_cleanup_skips_busy_reference_lock_without_holding_lifecycle_lock(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    lifecycle_released = threading.Event()
    attempted_reference = threading.Event()

    @contextmanager
    def operation(root=None, *, timeout=30):
        if root == tmp_path / "cleanup-coordination":
            attempted_reference.set()
            assert timeout == 0
            raise RuntimeError("busy reference lock")
        assert timeout == 0
        try:
            yield
        finally:
            lifecycle_released.set()

    monkeypatch.setattr(cleanup, "operation_lock", operation)

    report = cleanup.collect_runtime_garbage()

    assert attempted_reference.is_set()
    assert lifecycle_released.is_set()
    assert report["deleted"] == []
    assert report["error"] == "busy reference lock"


def test_uncertain_process_inventory_prevents_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    old = version(tmp_path / "bundles", "a")
    monkeypatch.setattr(cleanup, "process_references", lambda: None)
    assert cleanup.collect_runtime_garbage()["deleted"] == []
    assert old.exists()


def test_store_references_trash_and_grace_period_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    store = tmp_path / "store"
    old, used, recent = [version(store / "runtimes", c) for c in "abc"]
    os.utime(recent, (time.time(), time.time()))
    record = store / "trash" / "app" / "install.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"runtime": {"root": str(used)}}))
    cleanup.register_reference(store, store=True)
    assert str(old) in cleanup.collect_runtime_garbage()["deleted"]
    assert used.exists() and recent.exists()


def test_corrupt_reference_prevents_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    old = version(tmp_path / "bundles", "a")
    (tmp_path / "current-launcher.json").write_text("broken")
    assert cleanup.collect_runtime_garbage()["deleted"] == []
    assert old.exists()


@pytest.mark.parametrize("pointer", ["current-launcher.json", "previous-launcher.json"])
@pytest.mark.parametrize("content", [{}, {"command": [], "environment": {}}])
def test_semantically_invalid_launcher_prevents_cleanup(
    tmp_path, monkeypatch, pointer, content
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    old = version(tmp_path / "bundles", "a")
    (tmp_path / pointer).write_text(json.dumps(content), encoding="utf-8")

    report = cleanup.collect_runtime_garbage()

    assert report["deleted"] == []
    assert report["error"]
    assert old.exists()


def test_install_record_without_runtime_reference_prevents_cleanup(
    tmp_path, monkeypatch
):
    instance = tmp_path / "instance"
    store = tmp_path / "store"
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(instance))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    old = version(store / "runtimes", "a")
    record = store / "apps" / "demo" / "install.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"runtime": {}}), encoding="utf-8")
    cleanup.register_reference(store, store=True)

    report = cleanup.collect_runtime_garbage()

    assert report["deleted"] == []
    assert report["error"]
    assert old.exists()


def test_invalid_registered_reference_prevents_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    old = version(tmp_path / "bundles", "a")
    records = tmp_path / "runtime-references"
    records.mkdir()
    (records / "invalid.json").write_text(
        json.dumps({"path": "relative-project", "store": False}),
        encoding="utf-8",
    )

    report = cleanup.collect_runtime_garbage()

    assert report["deleted"] == []
    assert report["error"]
    assert old.exists()


def test_unreadable_reference_directory_prevents_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    old = version(tmp_path / "bundles", "a")
    records = tmp_path / "runtime-references"
    records.mkdir()
    original_scandir = cleanup.os.scandir

    def scandir(path):
        if Path(path) == records:
            raise PermissionError("reference records are unreadable")
        return original_scandir(path)

    monkeypatch.setattr(cleanup.os, "scandir", scandir)
    monkeypatch.setattr(
        cleanup.shutil,
        "rmtree",
        lambda path: pytest.fail("cleanup must not delete with incomplete references"),
    )

    report = cleanup.collect_runtime_garbage()

    assert report["deleted"] == []
    assert report["error"] == "reference records are unreadable"
    assert old.exists()
    saved = json.loads((tmp_path / "cleanup-report.json").read_text(encoding="utf-8"))
    assert saved["error"] == "reference records are unreadable"


def test_automatic_cleanup_skips_busy_repository_lock_and_releases_outer_locks(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    runtime = version(tmp_path / "bundles", "a")
    released: list[Path | None] = []

    @contextmanager
    def operation(root=None, *, timeout=30):
        assert timeout == 0
        if root == (tmp_path / "bundles").resolve():
            raise RuntimeError("busy repository lock")
        try:
            yield
        finally:
            released.append(root)

    monkeypatch.setattr(cleanup, "operation_lock", operation)

    report = cleanup.collect_runtime_garbage()

    assert report["deleted"] == []
    assert report["error"] == "busy repository lock"
    assert runtime.exists()
    assert None in released
    assert tmp_path / "cleanup-coordination" in released


def test_invalid_virtual_environment_record_prevents_cleanup(tmp_path, monkeypatch):
    instance = tmp_path / "instance"
    project = tmp_path / "project"
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(instance))
    monkeypatch.setattr(cleanup, "process_references", lambda: [])
    old = version(instance / "bundles", "a")
    project.mkdir()
    (project / "pyvenv.cfg").write_text(
        "home = relative-python\n", encoding="utf-8"
    )
    cleanup.register_reference(project)

    report = cleanup.collect_runtime_garbage()

    assert report["deleted"] == []
    assert report["error"]
    assert old.exists()


def test_running_mapping_and_failed_delete_are_retained(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    busy, locked = [version(tmp_path / "bundles", c) for c in "ab"]
    monkeypatch.setattr(cleanup, "process_references", lambda: [busy / "python.exe"])

    def deny(path):
        raise PermissionError("locked")

    monkeypatch.setattr(cleanup.shutil, "rmtree", deny)
    report = cleanup.collect_runtime_garbage()
    assert report["deleted"] == []
    assert report["skipped"][0]["path"] == str(locked)
    assert busy.exists() and locked.exists()


def test_launcher_rotation_accepts_legacy_and_corrupt_pointer(tmp_path, monkeypatch):
    from watcherobot.runtime.manager import save_launcher

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    old, new = [version(tmp_path / "bundles", c) for c in "ab"]
    pointer = tmp_path / "current-launcher.json"
    pointer.write_text(json.dumps([str(old / "python.exe")]))
    save_launcher([str(new / "python.exe")], {})
    previous = tmp_path / "previous-launcher.json"
    assert json.loads(previous.read_text())["command"] == [str(old / "python.exe")]
    pointer.write_text("broken")
    save_launcher([str(new / "python.exe")], {})
    assert json.loads(pointer.read_text())["command"] == [str(new / "python.exe")]
    assert json.loads(previous.read_text())["command"] == [str(old / "python.exe")]
