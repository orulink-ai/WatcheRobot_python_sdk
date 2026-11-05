import hashlib
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
from watcherobot.firmware.contract import FirmwareError
from watcherobot.firmware import operations
from watcherobot.firmware.platforms import FirmwarePlatform
from watcherobot.distribution.ports import AccessToken, CatalogDocument
from tests.distribution.test_firmware_installation import release_artifact
from watcherobot.firmware.transfer import release_record


def test_cache_removal_is_local_and_bounded_to_digest(tmp_path):
    _, data = release_artifact(tmp_path)
    digest = hashlib.sha256(data).hexdigest()
    packages = tmp_path / "cache" / "packages"
    packages.mkdir(parents=True)
    (packages / (digest + ".zip")).write_bytes(data)
    result = operations.execute(
        "remove_download",
        {"sha256": digest},
        provider="huggingface",
        cache_root=tmp_path / "cache",
        store_root=tmp_path / "store",
        progress=lambda **k: None,
    )
    assert result["removed"]
    assert not (packages / (digest + ".zip")).exists()
    with pytest.raises(FirmwareError):
        operations.execute(
            "remove_download",
            {"sha256": "../secret"},
            provider="huggingface",
            cache_root=tmp_path / "cache",
            store_root=tmp_path / "store",
            progress=lambda **k: None,
        )


@pytest.mark.parametrize("provider", ["huggingface", "gitee"])
def test_only_owner_can_request_withdrawal_and_review_is_required(
    tmp_path, monkeypatch, provider
):
    from watcherobot.firmware import platforms

    release, _ = release_artifact(tmp_path)
    if provider == "gitee":
        from dataclasses import replace

        release = replace(
            release,
            provider="gitee",
            path="",
            asset_id=19,
            download_url="https://gitee.com/api/v5/repos/author/fw/releases/17/attach_files/19/download",
        )
    calls = []
    import inspect
    from watcherobot.distribution.hf_publish import HuggingFacePublishHubClient

    def create(*args, **kwargs):
        inspect.signature(HuggingFacePublishHubClient.create_catalog_pull_request).bind(
            None, *args, **kwargs
        )
        calls.append(kwargs)
        return SimpleNamespace(url="https://example.com/review")

    hub = SimpleNamespace(
        list_open_catalog_pull_requests=lambda *a, **k: [],
        read_catalog=lambda *a, **k: CatalogDocument(
            commit="a" * 40,
            content=json.dumps(
                {"schema_version": 1, "firmwares": [release_record(release)]}
            ).encode(),
        ),
        create_catalog_pull_request=create,
    )
    monkeypatch.setattr(platforms, "HuggingFacePublishHubClient", lambda: hub)
    monkeypatch.setattr(platforms, "GiteeRepository", lambda: hub)

    def backend_for(username):
        result = FirmwarePlatform(
            provider,
            hf_factory=lambda token: SimpleNamespace(whoami=lambda: {"name": username}),
        )
        if provider == "gitee":
            monkeypatch.setattr(result, "_gitee", lambda *a, **k: {"login": username})
        return result

    backend = backend_for("another")
    with pytest.raises(FirmwareError, match="owner"):
        backend.request_withdrawal(
            release.firmware_id, release.version, AccessToken("unused")
        )
    assert not calls
    backend = backend_for("author")
    result = backend.request_withdrawal(
        release.firmware_id, release.version, AccessToken("unused")
    )
    assert result["status"] == "pending"
    assert json.loads(calls[0]["content"])["firmwares"][0]["withdrawn"] is True


def test_cached_legacy_package_reports_missing_layout_before_usb(tmp_path):
    from tests.distribution.test_firmware_contract import make_package

    source = tmp_path / "old.zip"
    make_package(source)
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    packages = tmp_path / "cache" / "packages"
    packages.mkdir(parents=True)
    (packages / (digest + ".zip")).write_bytes(data)
    result = operations.execute(
        "cache_status",
        {"sha256": digest},
        provider="huggingface",
        cache_root=tmp_path / "cache",
        store_root=tmp_path / "store",
        progress=lambda **k: None,
    )
    assert result["cached"] is True
    assert result["layout_verified"] is False
    assert "partition" in result["layout_error"]


def test_cached_complete_package_reports_verified_layout(tmp_path):
    _, data = release_artifact(tmp_path)
    digest = hashlib.sha256(data).hexdigest()
    packages = tmp_path / "cache" / "packages"
    packages.mkdir(parents=True)
    (packages / (digest + ".zip")).write_bytes(data)
    result = operations.execute(
        "cache_status",
        {"sha256": digest},
        provider="huggingface",
        cache_root=tmp_path / "cache",
        store_root=tmp_path / "store",
        progress=lambda **k: None,
    )
    assert result["layout_verified"] is True


def test_cache_deletion_rejects_linked_package_directory(tmp_path, monkeypatch):
    _, data = release_artifact(tmp_path)
    digest = hashlib.sha256(data).hexdigest()
    packages = tmp_path / "cache" / "packages"
    packages.mkdir(parents=True)
    target = packages / (digest + ".zip")
    target.write_bytes(data)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == packages or original(p))
    with pytest.raises(FirmwareError, match="symbolic"):
        operations.execute(
            "remove_download",
            {"sha256": digest},
            provider="huggingface",
            cache_root=tmp_path / "cache",
            store_root=tmp_path / "store",
            progress=lambda **k: None,
        )
    assert target.exists()
