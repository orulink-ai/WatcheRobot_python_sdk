import json

import pytest

from watcherobot.firmware import operations
from watcherobot.firmware.contract import FirmwareError
from watcherobot.firmware.transfer import release_record
from tests.distribution.test_firmware_transfer import artifact


def setup_catalog(tmp_path):
    release, data = artifact(tmp_path)
    root = tmp_path / "cache"
    operations._write(
        root / "huggingface/catalog.json",
        {"schema_version": 1, "firmwares": [release_record(release)]},
    )
    (root / "packages").mkdir()
    (root / "packages" / (release.sha256 + ".zip")).write_bytes(data)
    return release, root


def editable_project(tmp_path):
    from tests.distribution.test_firmware_application import document

    project = tmp_path / "project"
    project.mkdir()
    original = document()
    del original["requires_firmware"]
    path = project / "app.json"
    path.write_text(json.dumps(original), encoding="utf-8")
    request = {"application_dir": str(project), "requires_firmware": {"mode": "not_required"}}

    def configure(value):
        return operations.execute("configure", value, provider="huggingface",
                                  cache_root=tmp_path / "cache", store_root=tmp_path,
                                  progress=lambda **_: None)

    preview = configure(request)
    return path, {**request, "confirmed": True, "preview_key": preview["preview_key"]}, configure


@pytest.mark.skipif(operations.os.name != "posix", reason="POSIX directory permissions")
def test_configure_is_not_blocked_by_another_users_global_temp_locks(tmp_path, monkeypatch):
    shared_temp = tmp_path / "shared-temp"
    legacy_locks = shared_temp / "watcherobot-manifest-locks"
    legacy_locks.mkdir(parents=True)
    legacy_locks.chmod(0o500)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(operations.tempfile, "gettempdir", lambda: str(shared_temp))
    monkeypatch.setattr(operations.Path, "home", lambda: home)
    try:
        if operations.os.access(legacy_locks, operations.os.W_OK):
            pytest.skip("Current account bypasses read-only directory permissions")
        path, request, configure = editable_project(tmp_path)
        assert configure(request)["configured"] is True
        assert json.loads(path.read_bytes())["requires_firmware"] == {"mode": "not_required"}
        assert not list(legacy_locks.iterdir())
        assert list((home / ".watcherobot" / "manifest-locks").glob("*.lock"))
    finally:
        legacy_locks.chmod(0o700)


def test_configure_preserves_external_edit_during_temporary_file_write(tmp_path, monkeypatch):
    path, request, configure = editable_project(tmp_path)
    edited = json.loads(path.read_bytes())
    edited["author"] = "Edited during save"
    dump = operations.json.dump

    def edit_after_serializing(*args, **kwargs):
        dump(*args, **kwargs)
        path.write_text(json.dumps(edited), encoding="utf-8")

    monkeypatch.setattr(operations.json, "dump", edit_after_serializing)
    with pytest.raises(FirmwareError, match="changed"):
        configure(request)
    assert json.loads(path.read_bytes()) == edited


def test_concurrent_configuration_accepts_only_one_confirmed_preview(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _, request, configure = editable_project(tmp_path)
    first_entered, second_entered = Event(), Event()
    write = operations._write

    def delayed_write(*args, **kwargs):
        if not first_entered.is_set():
            first_entered.set()
            second_entered.wait(timeout=1)
        else:
            second_entered.set()
        return write(*args, **kwargs)

    monkeypatch.setattr(operations, "_write", delayed_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(configure, request)
        assert first_entered.wait(timeout=5)
        second = pool.submit(configure, request)
        assert first.result(timeout=5)["configured"] is True
        with pytest.raises(FirmwareError, match="changed"):
            second.result(timeout=5)


def test_offline_download_uses_verified_catalog_cache(tmp_path, monkeypatch):
    release, root = setup_catalog(tmp_path)
    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", lambda _: (_ for _ in ()).throw(OSError("offline")))
    result = operations.execute(
        "download",
        {"firmware_id": release.firmware_id, "version": "v" + release.version},
        provider="huggingface",
        cache_root=root,
        store_root=tmp_path,
        progress=lambda **e: None,
    )
    assert result["release"]["sha256"] == release.sha256
    assert result["catalog_stale"] is True


@pytest.mark.parametrize("mode", [None, "not_required", "required"])
def test_app_requirement_is_checked_before_catalog_access(tmp_path, monkeypatch, mode):
    from tests.distribution.test_firmware_application import document
    value = document()
    if mode is None:
        del value["requires_firmware"]
    elif mode == "not_required":
        value["requires_firmware"] = {"mode": mode}
    source = tmp_path / "store/apps" / value["id"] / "source"
    source.mkdir(parents=True)
    (source / "app.json").write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", lambda _: (_ for _ in ()).throw(OSError("offline")))
    def download():
        return operations.execute("download", {"app_id": value["id"]}, provider="huggingface",
                                  cache_root=tmp_path / "cache", store_root=tmp_path / "store", progress=lambda **_: None)
    if mode == "required":
        with pytest.raises(FirmwareError):
            download()
    else:
        assert download()["required"] is False


def test_online_withdrawal_blocks_previously_cached_package(tmp_path, monkeypatch):
    from dataclasses import replace
    release, root = setup_catalog(tmp_path)
    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", lambda _: ("b" * 40, (replace(release, withdrawn=True),)))
    with pytest.raises(FirmwareError, match="withdrawn"):
        operations.execute("download", {"firmware_id": release.firmware_id, "version": release.version},
                           provider="huggingface", cache_root=root, store_root=tmp_path, progress=lambda **_: None)


def test_offline_mode_never_downloads_a_new_or_corrupt_package(tmp_path, monkeypatch):
    from watcherobot.firmware import transfer
    release, root = setup_catalog(tmp_path)
    (root / "packages" / (release.sha256 + ".zip")).write_bytes(b"corrupt cache")
    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", lambda _: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(transfer.httpx, "Client", lambda **_: pytest.fail("offline cache reuse must not download"))
    for _ in range(2):
        with pytest.raises(FirmwareError, match="catalog"):
            operations.execute("download", {"firmware_id": release.firmware_id, "version": release.version},
                               provider="huggingface", cache_root=root, store_root=tmp_path, progress=lambda **_: None)


@pytest.mark.parametrize("action", ["marketplace", "download"])
def test_catalog_persistence_failure_cannot_fall_back_to_old_approval(tmp_path, monkeypatch, action):
    from dataclasses import replace
    release, root = setup_catalog(tmp_path)
    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", lambda _: ("b" * 40, (replace(release, withdrawn=True),)))
    monkeypatch.setattr(operations, "_write", lambda *a, **kw: (_ for _ in ()).throw(OSError("read only")))
    with pytest.raises(FirmwareError, match="catalog"):
        operations.execute(action, {"firmware_id": release.firmware_id, "version": release.version},
                           provider="huggingface", cache_root=root, store_root=tmp_path, progress=lambda **_: None)
    # A fresh interpreter must honor the on-disk veto, not a process-local flag.
    import subprocess
    import sys
    script = """
import sys
from pathlib import Path
from watcherobot.firmware import operations
from watcherobot.firmware.contract import FirmwareError
try:
    operations._releases(Path(sys.argv[1]), 'huggingface')
except FirmwareError:
    sys.exit(0)
sys.exit(1)
"""
    completed = subprocess.run([sys.executable, "-c", script, str(root)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", lambda _: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(FirmwareError, match="catalog|withdrawn"):
        operations.execute("download", {"firmware_id": release.firmware_id, "version": release.version},
                           provider="huggingface", cache_root=root, store_root=tmp_path, progress=lambda **_: None)


@pytest.mark.parametrize("cache_unlink_fails", [False, True])
def test_failed_catalog_veto_falls_back_to_cache_invalidation(tmp_path, monkeypatch, cache_unlink_fails):
    from dataclasses import replace

    release, root = setup_catalog(tmp_path)
    marker = operations._catalog_state(root, "huggingface").with_suffix(".invalid")
    catalog_path = root / "huggingface/catalog.json"
    original_open, original_unlink = operations.Path.open, operations.Path.unlink

    def denied_marker(path, *args, **kwargs):
        if path == marker:
            raise PermissionError("marker read only")
        return original_open(path, *args, **kwargs)

    def denied_unlink(path, *args, **kwargs):
        if path == catalog_path and cache_unlink_fails:
            raise PermissionError("cache read only")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(operations.Path, "open", denied_marker)
    monkeypatch.setattr(operations.Path, "unlink", denied_unlink)
    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", lambda _: ("b" * 40, (replace(release, withdrawn=True),)))
    expected = "cannot persist across restart" if cache_unlink_fails else "catalog"
    with pytest.raises(FirmwareError, match=expected):
        operations.execute("marketplace", {}, provider="huggingface", cache_root=root,
                           store_root=tmp_path, progress=lambda **_: None)
    assert catalog_path.exists() == cache_unlink_fails
    with pytest.raises(FirmwareError, match="catalog"):
        operations._releases(root, "huggingface")


def _catalog_transaction_worker(root, home, release, action, withdrawn, started, entered, resume, results, patches=True):
    """Spawn-safe real execute worker; only remote catalog transport is replaced."""
    from dataclasses import replace
    from unittest.mock import patch
    from contextlib import nullcontext

    def catalog(_):
        entered.set()
        if not resume.wait(10):
            raise RuntimeError("test did not release remote response")
        if withdrawn is None:
            raise OSError("offline")
        return "b" * 40, (replace(release, withdrawn=withdrawn),)

    with (patch.object(operations.Path, "home", return_value=home) if patches else nullcontext()), (
        patch.object(operations.FirmwarePlatform, "catalog", catalog) if patches else nullcontext()
    ):
        started.set()
        try:
            operations.execute(action, {"firmware_id": release.firmware_id, "version": release.version},
                               provider="huggingface", cache_root=root, store_root=root.parent,
                               progress=lambda **_: None)
            results.put("ok")
        except FirmwareError as error:
            results.put(str(error))


@pytest.mark.parametrize("workers", ["threads", "processes"])
@pytest.mark.parametrize("first_action", ["marketplace", "download"])
@pytest.mark.parametrize("second_action", ["marketplace", "download"])
def test_catalog_transactions_prevent_late_old_approval(tmp_path, monkeypatch, workers, first_action, second_action):
    import multiprocessing
    import queue
    import threading

    release, root = setup_catalog(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(operations.Path, "home", lambda: home)
    alias = tmp_path / "cache-alias"
    alias.symlink_to(root, target_is_directory=True)
    context = multiprocessing.get_context("spawn") if workers == "processes" else threading
    results = multiprocessing.get_context("spawn").Queue() if workers == "processes" else queue.Queue()
    first_started, second_started = context.Event(), context.Event()
    first_entered, second_entered = context.Event(), context.Event()
    resume, immediate = context.Event(), context.Event()
    immediate.set()
    worker = multiprocessing.get_context("spawn").Process if workers == "processes" else threading.Thread
    if workers == "threads":
        from dataclasses import replace

        def catalog(_):
            withdrawn = threading.current_thread().name == "new-withdrawal"
            (second_entered if withdrawn else first_entered).set()
            assert (immediate if withdrawn else resume).wait(10)
            return "b" * 40, (replace(release, withdrawn=withdrawn),)

        monkeypatch.setattr(operations.FirmwarePlatform, "catalog", catalog)
    first = worker(target=_catalog_transaction_worker, args=(root, home, release, first_action, False,
                                                           first_started, first_entered, resume, results, workers == "processes"), name="old-approval")
    second = worker(target=_catalog_transaction_worker, args=(alias, home, release, second_action, True,
                                                            second_started, second_entered, immediate, results, workers == "processes"), name="new-withdrawal")
    first.start()
    try:
        assert first_entered.wait(10)
        second.start()
        assert second_started.wait(10)
        overlapped = second_entered.wait(0.5)
        if overlapped:
            # In the broken implementation the newer withdrawal commits first;
            # only then allow the older approval response to finish saving.
            second.join(10)
            assert not second.is_alive()
    finally:
        resume.set()
        first.join(15)
        if second.ident is not None:
            second.join(15)
    assert not first.is_alive() and not second.is_alive()
    outcomes = [results.get(timeout=5), results.get(timeout=5)]
    assert all(value == "ok" or "withdrawn" in value or "catalog" in value for value in outcomes)
    assert operations.parse_catalog((root / "huggingface/catalog.json").read_bytes(),
                                    provider="huggingface")[0].withdrawn, outcomes
    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", lambda _: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(FirmwareError, match="withdrawn"):
        operations.execute("download", {"firmware_id": release.firmware_id, "version": release.version},
                           provider="huggingface", cache_root=root, store_root=tmp_path, progress=lambda **_: None)
    assert not overlapped, "remote fetch and persistence must share a transaction lock"


def test_offline_process_waits_for_inflight_withdrawal(tmp_path):
    import multiprocessing

    release, root = setup_catalog(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    entered, offline_entered = context.Event(), context.Event()
    started, offline_started = context.Event(), context.Event()
    resume, immediate = context.Event(), context.Event()
    immediate.set()
    refresh = context.Process(target=_catalog_transaction_worker,
                              args=(root, home, release, "marketplace", True, started, entered, resume, results))
    offline = context.Process(target=_catalog_transaction_worker,
                              args=(root, home, release, "download", None, offline_started, offline_entered, immediate, results))
    refresh.start()
    try:
        assert entered.wait(10)
        offline.start()
        assert offline_started.wait(10)
        overlapped = offline_entered.wait(0.5)
    finally:
        resume.set()
        refresh.join(15)
        if offline.ident is not None:
            offline.join(15)
    assert not refresh.is_alive() and not offline.is_alive()
    outcomes = [results.get(timeout=5), results.get(timeout=5)]
    assert "ok" in outcomes
    assert any("withdrawn" in value for value in outcomes), outcomes
    assert not overlapped


def test_dependency_edit_is_previewed_and_requires_confirmation(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    from tests.distribution.test_firmware_application import document

    original = document(3)
    del original["requires_firmware"]
    original["author"] = "Keep me"
    path = root / "app.json"
    path.write_text(json.dumps(original))
    request = {
        "application_dir": str(root),
        "requires_firmware": {"mode": "not_required"},
    }

    def execute(req):
        return operations.execute(
            "configure",
            req,
            provider="huggingface",
            cache_root=tmp_path / "cache",
            store_root=tmp_path,
            progress=lambda **e: None,
        )

    preview = execute(request)
    assert preview["preview"]["schema_version"] == 3
    assert json.loads(path.read_text()) == original
    with pytest.raises(FirmwareError):
        execute({**request, "confirmed": True, "preview_key": "stale"})
    execute({**request, "confirmed": True, "preview_key": preview["preview_key"]})
    assert json.loads(path.read_text())["author"] == "Keep me"


def test_marketplace_network_failure_retains_cache_and_warns(tmp_path, monkeypatch):
    release, root = setup_catalog(tmp_path)

    def unavailable(*args, **kwargs):
        raise OSError("connection failed with private detail")

    monkeypatch.setattr(operations.FirmwarePlatform, "catalog", unavailable)
    from watcherobot.distribution import marketplace

    monkeypatch.setattr(marketplace, "load_official_marketplace", unavailable)
    result = operations.execute(
        "marketplace",
        {},
        provider="huggingface",
        cache_root=root,
        store_root=tmp_path,
        progress=lambda **e: None,
    )
    assert result["firmwares"][0]["sha256"] == release.sha256
    assert len(result["warnings"]) == 1
    assert "private detail" not in str(result)
    assert result["stale"] is True


def test_sdk_reads_history_without_managing_desktop_jobs(tmp_path):
    from filelock import FileLock

    root = tmp_path / "cache"
    operations._write(root / "jobs/active.json", {"id": "active", "status": "running"})
    operations._write(
        root / "jobs/abandoned.json", {"id": "abandoned", "status": "running"}
    )
    with FileLock(str(root / "jobs/active.json.lock"), timeout=0):
        result = operations.execute(
            "history",
            {},
            provider="huggingface",
            cache_root=root,
            store_root=tmp_path,
            progress=lambda **e: None,
        )
    states = {item["id"]: item["status"] for item in result["jobs"]}
    assert states == {"active": "running", "abandoned": "running"}


def test_publication_cannot_use_local_cache_as_public_availability_proof(
    tmp_path, monkeypatch
):
    import httpx
    from watcherobot.distribution.ports import AccessToken
    from watcherobot.firmware.transfer import acquire_package

    release, root = setup_catalog(tmp_path)
    monkeypatch.setattr(
        operations.SystemCredentialStore, "load", lambda self: AccessToken("secret")
    )
    monkeypatch.setattr(
        operations.FirmwarePlatform, "publish", lambda *a, **kw: release
    )
    requests = []

    def unavailable(request):
        requests.append(request)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(unavailable)) as client:
        monkeypatch.setattr(
            operations,
            "acquire_package",
            lambda r, p, progress: acquire_package(
                r, p, client=client, progress=progress
            ),
        )
        with pytest.raises(FirmwareError, match="404"):
            operations.execute(
                "publish",
                {"package_path": "unused.zip"},
                provider="huggingface",
                cache_root=root,
                store_root=tmp_path,
                progress=lambda **e: None,
            )
    assert len(requests) == 1
    assert not list((root / "huggingface/publications").glob("*.json"))
