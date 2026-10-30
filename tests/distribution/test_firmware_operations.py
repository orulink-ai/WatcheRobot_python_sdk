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


def test_offline_download_uses_verified_catalog_cache(tmp_path):
    release, root = setup_catalog(tmp_path)
    result = operations.execute(
        "download",
        {"firmware_id": release.firmware_id, "version": "v" + release.version},
        provider="huggingface",
        cache_root=root,
        store_root=tmp_path,
        progress=lambda **e: None,
    )
    assert result["release"]["sha256"] == release.sha256


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
