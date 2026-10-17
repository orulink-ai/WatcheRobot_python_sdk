from pathlib import Path

from watcherobot.management.service import Manager


def request():
    return {
        "desktop": {
            "manager": ">=1,<2",
            "management": ">=1,<2",
            "distribution": ">=1,<2",
        },
        "platform": "darwin-arm64",
        "daemon_args": [],
    }


def test_status_works_without_daemon_or_network(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    state = manager.status()
    assert state["selection"]["version"]
    assert state["daemon"]["state"] == "stopped"
    assert state["compatibility"]["status"] == "compatible"
    assert state["actions"]["status"]["enabled"] is True
    assert state["actions"]["check_update"]["enabled"] is False


def test_actual_daemon_version_does_not_overwrite_selection(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    live = {
        "runtime": {
            "sdk_version": "external",
            "build_id": "source:external",
            "contracts": {
                "manager": 1,
                "management": 1,
                "distribution": 1,
                "application": 1,
                "state": 1,
            },
        },
        "application": {"state": "not_running"},
    }
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: live)
    state = manager.status()
    assert state["selection"]["version"] != "external"
    assert state["daemon"]["version"] == "external"
    assert state["actions"]["restore_default_runtime"]["enabled"] is True


def test_desktop_incompatibility_disables_mutation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    req = request()
    req["desktop"]["management"] = ">=2,<3"
    manager = Manager(req, root=tmp_path / "manager", live=lambda: None)
    state = manager.status()
    assert state["compatibility"]["status"] == "incompatible"
    assert state["actions"]["update"]["enabled"] is False


def test_status_reports_broken_environment_and_offers_repair(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    state = manager.store.snapshot()
    state["selection"] = {
        "kind": "environment",
        "root": str(manager.store.root / "environments" / ("a" * 64)),
        "version": "0.1.13",
        "build_id": "source:missing",
        "contracts": {
            "manager": 1,
            "management": 1,
            "distribution": 1,
            "application": 1,
            "state": 1,
        },
    }
    manager.store.save(state)
    snapshot = manager.status()
    assert snapshot["selection"]["health"] == "broken"
    assert snapshot["actions"]["repair"]["enabled"]
    assert snapshot["actions"]["update"]["enabled"] is False


def test_control_request_rejects_unverified_daemon_before_dispatch(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(
        request(),
        root=tmp_path / "manager",
        live=lambda: {"runtime": {"sdk_version": "legacy"}, "application": {}},
    )
    result = manager.control("POST", "/daemon/application/start", None)
    assert result["status"] == 426
    assert result["payload"]["error"] == "daemon_contract_unverified"


def test_control_request_cannot_target_business_or_remote_endpoint(
    tmp_path: Path, monkeypatch
):
    import pytest

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    with pytest.raises(ValueError):
        manager.control("POST", "https://example.com/daemon/start", None)


def test_desktop_ota_is_blocked_by_running_application(tmp_path: Path, monkeypatch):
    import os

    import pytest

    from watcherobot.management.activity import ActivityBusy

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(
        request(),
        root=tmp_path / "manager",
        live=lambda: {"application": {"state": "running"}},
    )
    with pytest.raises(ActivityBusy):
        manager.begin_activity("desktop_ota", os.getpid())


def test_selected_distribution_entry_is_executable(tmp_path: Path, monkeypatch):
    import subprocess
    import sys

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    import json

    from watcherobot.management.compatibility import runtime_contracts

    root = manager.store.root / "environments" / ("a" * 64)
    root.mkdir(parents=True)
    target = {
        "kind": "environment",
        "root": str(root),
        "version": "0.1.12",
        "build_id": "source:test",
        "contracts": runtime_contracts(),
    }
    (root / "sdk-environment.json").write_text(json.dumps(target))
    (root / "pyvenv.cfg").write_text("test")
    monkeypatch.setattr(
        "watcherobot.management.service.environment_python",
        lambda root: Path(sys.executable),
    )
    command = manager.command(target, entry="distribution")
    result = subprocess.run(
        [*command, "--help"], capture_output=True, text=True, check=True
    )
    assert "watcher-distribution" in result.stdout


def test_rollback_command_rejects_environment_outside_manager(tmp_path, monkeypatch):
    import pytest

    from watcherobot.management.compatibility import runtime_contracts

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    with pytest.raises(ValueError, match="managed repository"):
        manager.command(
            {
                "kind": "environment",
                "root": str(tmp_path / "outside"),
                "version": "0.1.12",
                "build_id": "source:test",
                "contracts": runtime_contracts(),
            }
        )


def test_update_check_explains_latest_release_requiring_newer_desktop(
    tmp_path, monkeypatch
):
    from watcherobot.management.releases import verify_catalog

    from .test_releases import release, signed

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    blob, keys = signed(
        {
            "schema_version": 1,
            "sequence": 1,
            "releases": [release("0.1.13"), release("0.1.14", 2)],
        }
    )
    catalog = verify_catalog(blob, keys)

    class Mirror:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def catalog(self, public_keys):
            return catalog, blob

    monkeypatch.setattr("watcherobot.management.mirror.Mirror", Mirror)
    manager.public_keys = keys
    state = manager.check_update()
    assert state["recommendation"]["version"] == "0.1.13"
    assert state["latest_unavailable"]["version"] == "0.1.14"
    assert state["latest_unavailable"]["reason"] == "desktop_contract_incompatible"


def test_interrupted_update_allows_bundled_repair_when_recovery_cannot_load_old_sdk(
    tmp_path, monkeypatch
):
    import pytest

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    with pytest.raises(KeyboardInterrupt), manager.store.operation("update") as task:
        manager.store.transition(task, "verifying")
        raise KeyboardInterrupt()
    assert manager.status()["actions"]["repair"]["enabled"]
    called = []

    def activate(store, target, fallback, runtime):
        called.append(target)
        state = store.snapshot()
        state.update(selection=target, transaction=None, candidate=None)
        store.save(state)

    monkeypatch.setattr("watcherobot.management.deployment.activate", activate)
    manager.repair()
    assert called[0]["kind"] == "bundled"
    assert not manager.store.needs_recovery()


def test_business_gate_uses_declarations_and_does_not_special_case_default_app(
    tmp_path, monkeypatch
):
    from watcherobot.management.compatibility import runtime_contracts

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    req = {
        **request(),
        "desktop_business": {
            "device_control": ">=1,<2",
            "application_settings": ">=1,<2",
        },
    }
    live = {
        "runtime": {"contracts": runtime_contracts()},
        "application": {"current_app": "watcher_default", "state": "running"},
    }
    manager = Manager(req, root=tmp_path / "manager", live=lambda: live)
    assert (
        manager.business_state(live)["groups"]["device_control"]["status"]
        == "unverified"
    )
    live["application"]["desktop_contracts"] = {"device_control": 1}
    assert manager.business_state(live)["groups"]["device_control"]["enabled"] is True
    assert (
        manager.business_state(live)["groups"]["application_settings"]["enabled"]
        is False
    )
    live["application"]["state"] = "not_running"
    assert manager.business_state(live)["mode"] == "transparent"
    assert manager.business_state(live)["groups"]["device_control"]["enabled"] is True
    assert (
        manager.business_state(live)["groups"]["application_settings"]["enabled"]
        is False
    )


def test_control_stop_keeps_the_application_shutdown_window(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import httpx

    import watcherobot.cli
    from watcherobot.management.compatibility import runtime_contracts

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setattr(
        watcherobot.cli,
        "_live_runtime_state",
        lambda: SimpleNamespace(control_url="http://127.0.0.1:1"),
    )
    manager = Manager(
        request(),
        root=tmp_path / "manager",
        live=lambda: {"runtime": {"contracts": runtime_contracts()}},
    )
    original = httpx.Client
    timeouts = []

    def client(**kwargs):
        timeouts.append(kwargs["timeout"])
        return original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200, json={"application": {"state": "not_running"}}
                )
            ),
        )

    monkeypatch.setattr(httpx, "Client", client)
    assert manager.control("POST", "/daemon/application/stop", None)["status"] == 200
    assert timeouts == [120]


def test_control_bounds_streaming_response_before_buffering(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import httpx
    import pytest

    import watcherobot.cli
    from watcherobot.management.compatibility import runtime_contracts

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setattr(
        watcherobot.cli,
        "_live_runtime_state",
        lambda: SimpleNamespace(control_url="http://127.0.0.1:1"),
    )
    manager = Manager(
        request(),
        root=tmp_path / "manager",
        live=lambda: {"runtime": {"contracts": runtime_contracts()}},
    )
    original = httpx.Client
    chunks = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(20):
                chunks.append(index)
                yield b"x" * (1024 * 1024)

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, stream=Stream())
            ),
        ),
    )
    with pytest.raises(ValueError, match="exceeds limit"):
        manager.control("GET", "/daemon/status", None)
    assert len(chunks) <= 5


def test_control_rejects_noncanonical_management_paths(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    for path in (
        "/daemon/../business",
        "/daemon/%2e%2e/business",
        "/daemon/status#ignored",
        "/daemon/\\..\\business",
        "/daemon/status\n",
    ):
        with pytest.raises(ValueError, match="Only local"):
            manager.control("GET", path, None)


def test_update_cleans_retired_versions_before_space_check_and_candidate(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    import pytest

    from watcherobot.management.compatibility import runtime_contracts

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    release = SimpleNamespace(
        version="99.0.0",
        identity="next",
        contracts=runtime_contracts(),
        artifacts=[SimpleNamespace(size=1)],
    )
    monkeypatch.setattr(manager, "_locked_release", lambda identity: release)
    monkeypatch.setattr(manager, "_application_impacts", lambda release: [])
    events = []
    monkeypatch.setattr(
        "watcherobot.management.cleanup.retire_previous",
        lambda *args, **kwargs: events.append("retire"),
    )
    monkeypatch.setattr(
        "watcherobot.management.cleanup.clean_environments",
        lambda *args, **kwargs: (
            events.append("clean") or {"removed": 1, "deferred": False, "errors": []}
        ),
    )
    monkeypatch.setattr(
        "shutil.disk_usage",
        lambda root: events.append("space") or SimpleNamespace(free=10**12),
    )
    monkeypatch.setattr(
        manager,
        "_runtime_resources",
        lambda: (_ for _ in ()).throw(RuntimeError("resource checkpoint")),
    )
    with pytest.raises(RuntimeError, match="resource checkpoint"):
        manager.update()
    assert events == ["retire", "clean", "space"]
    assert manager.store.snapshot()["cleanup"]["removed"] == 1


def test_status_diagnostics_exposes_versions_and_task_without_paths_or_secrets(
    tmp_path, monkeypatch
):
    import json

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    req = {
        **request(),
        "source_launch_environment": [["PRIVATE_TOKEN", "do-not-export"]],
    }
    manager = Manager(req, root=tmp_path / "private-location", live=lambda: None)
    with manager.store.operation("check_update"):
        pass
    diagnostic = manager.status()["diagnostics"]
    assert diagnostic["selection"]["version"]
    assert diagnostic["operation"]["id"]
    assert "do-not-export" not in json.dumps(diagnostic)
    assert str(tmp_path) not in json.dumps(diagnostic)


def test_recover_discards_interrupted_downloads_and_reports_cleanup(
    tmp_path, monkeypatch
):
    import pytest

    from watcherobot.management.cleanup import clean_environments

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    downloads = manager.store.root / "downloads"
    downloads.mkdir()
    (downloads / "interrupted.whl.part").write_bytes(b"partial download")
    with pytest.raises(KeyboardInterrupt), manager.store.operation("update") as task:
        manager.store.transition(task, "downloading")
        raise KeyboardInterrupt()
    monkeypatch.setattr(
        "watcherobot.management.cleanup.clean_environments",
        lambda store: clean_environments(store, list),
    )
    result = manager.recover()
    assert not downloads.exists()
    assert result["download_cleanup"] == {"deferred": False, "errors": []}
    assert not manager.store.needs_recovery()


def test_cancellation_after_final_download_removes_files_without_installing(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    import pytest

    from watcherobot.management.compatibility import runtime_contracts
    from watcherobot.management.state import ManagerCancelled

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    manager = Manager(request(), root=tmp_path / "manager", live=lambda: None)
    release = SimpleNamespace(
        version="99.0.0",
        identity="next",
        contracts=runtime_contracts(),
        artifacts=[SimpleNamespace(size=1)],
    )
    monkeypatch.setattr(manager, "_locked_release", lambda identity: release)
    monkeypatch.setattr(manager, "_application_impacts", lambda release: [])
    monkeypatch.setattr(manager, "_runtime_resources", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "watcherobot.management.cleanup.clean_environments",
        lambda store: {"removed": 0, "deferred": False, "errors": []},
    )
    monkeypatch.setattr(
        "watcherobot.management.installer.install_environment",
        lambda *a, **k: pytest.fail("cancelled SDK must not install"),
    )

    class Mirror:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def download(self, artifact, root, checkpoint, *, progress):
            root.mkdir()
            file = root / "last.whl"
            file.write_bytes(b"x")
            manager.store.request_cancel(manager.store.snapshot()["operation"]["id"])
            return file

    monkeypatch.setattr("watcherobot.management.mirror.Mirror", Mirror)
    with pytest.raises(ManagerCancelled):
        manager.update()
    assert not (manager.store.root / "downloads").exists()
    assert manager.store.snapshot()["selection"] is None
    assert manager.store.snapshot()["operation"]["phase"] == "cancelled"
