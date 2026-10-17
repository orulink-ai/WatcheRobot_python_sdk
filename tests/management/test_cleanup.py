from pathlib import Path

from watcherobot.management.cleanup import clean_environments
from watcherobot.management.state import ManagerStore


def test_cleanup_protects_selection_process_and_external_link(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    store = ManagerStore(tmp_path / "manager")
    root = store.root / "environments"
    root.mkdir()
    current = root / ("a" * 64)
    running = root / ("b" * 64)
    old = root / ("c" * 64)
    for p in [current, running, old]:
        p.mkdir()
    state = store.snapshot()
    state["selection"] = {"root": str(current)}
    store.save(state)
    external = tmp_path / "external"
    external.mkdir()
    (root / ("d" * 64)).symlink_to(external, target_is_directory=True)
    report = clean_environments(store, lambda: [running / "bin/python"])
    assert current.exists() and running.exists() and external.exists()
    assert not old.exists()
    assert report["removed"] == 1


def test_missing_process_visibility_defers_cleanup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    store = ManagerStore(tmp_path / "manager")
    old = store.root / "environments" / ("c" * 64)
    old.mkdir(parents=True)
    assert clean_environments(store, lambda: None)["deferred"] is True
    assert old.exists()


def test_expired_rollback_is_retired_but_live_version_is_kept(
    tmp_path: Path, monkeypatch
):
    from watcherobot.management.cleanup import retire_previous

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    store = ManagerStore(tmp_path / "manager")
    old = store.root / "environments" / ("e" * 64)
    old.mkdir(parents=True)
    state = store.snapshot()
    state["previous"] = {"root": str(old), "retained_at": 0}
    store.save(state)
    assert (
        retire_previous(
            store, now=8 * 24 * 3600, references=lambda: [old / "bin/python"]
        )
        is False
    )
    assert store.snapshot()["previous"] is not None
    assert retire_previous(store, now=8 * 24 * 3600, references=list) is True
    assert store.snapshot()["previous"] is None


def test_download_cleanup_reports_failure_without_following_links(
    tmp_path, monkeypatch
):
    from watcherobot.management.cleanup import clean_downloads
    from watcherobot.management.state import ManagerStore

    store = ManagerStore(tmp_path / "manager")
    outside = tmp_path / "application"
    outside.mkdir()
    (outside / "preserve").write_text("keep")
    (store.root / "downloads").symlink_to(outside, target_is_directory=True)
    result = clean_downloads(store)
    assert result["deferred"] is True
    assert result["errors"]
    assert (outside / "preserve").read_text() == "keep"


def test_cache_cleanup_uses_sdk_private_cache_and_never_forces_active_files(tmp_path):
    from watcherobot.management.cleanup import clean_cache

    store = ManagerStore(tmp_path / "manager")
    (store.root / "cache").mkdir()
    calls = []

    class Runner:
        def run(self, command):
            calls.append(command)

    result = clean_cache(store, tmp_path / "uv", runner=Runner())
    assert not result["deferred"]
    assert "--force" not in calls[0].arguments
    assert str(store.root / "cache") in calls[0].arguments
    assert "--offline" in calls[0].arguments
