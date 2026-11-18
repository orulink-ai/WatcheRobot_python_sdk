"""Marketplace publication needs a self-contained layout; local updates do not."""

from dataclasses import replace
from functools import partial

import httpx
import pytest

from watcherobot.distribution.ports import AccessToken
from watcherobot.firmware import operations, platforms
from watcherobot.firmware.contract import FirmwareError
from watcherobot.firmware.installation import inspect_installation
from watcherobot.firmware.platforms import FirmwarePlatform
from watcherobot.firmware.preparation import prepare_package
from watcherobot.firmware.transfer import acquire_package, validate_release
from tests.distribution.test_firmware_installation import package, release_artifact


def no_remote(*args, **kwargs):
    pytest.fail("Incomplete publication must fail before provider or credential access")


@pytest.mark.parametrize("provider", ["gitee", "huggingface"])
@pytest.mark.parametrize("entry", ["publish", "_publish"])
def test_application_update_is_not_a_complete_publication(tmp_path, provider, entry):
    source = package(tmp_path / "update.zip")
    with httpx.Client(transport=httpx.MockTransport(no_remote)) as client:
        backend = FirmwarePlatform(provider, client=client, hf_factory=no_remote)
        with pytest.raises(FirmwareError, match="complete installation.*bootloader.*partition table"):
            getattr(backend, entry)(source, AccessToken("unused"))


@pytest.mark.parametrize("provider", ["gitee", "huggingface"])
def test_execute_rejects_update_before_credentials(tmp_path, monkeypatch, provider):
    source = package(tmp_path / "update.zip")
    monkeypatch.setattr(operations.SystemCredentialStore, "load", no_remote)
    monkeypatch.setattr(operations.FirmwarePlatform, "publish", no_remote)
    with pytest.raises(FirmwareError, match="complete installation"):
        operations.execute(
            "publish", {"package_path": str(source)}, provider=provider,
            cache_root=tmp_path / "cache", store_root=tmp_path,
            progress=lambda **_: None,
        )
    assert not list((tmp_path / "cache").rglob("publications/*.json"))


@pytest.mark.parametrize("provider", ["gitee", "huggingface"])
def test_submit_rechecks_historical_update_before_catalog_access(tmp_path, monkeypatch, provider):
    release, data = release_artifact(tmp_path)
    if provider == "gitee":
        release = replace(
            release, provider=provider, path="", asset_id=29,
            download_url="https://gitee.com/api/v5/repos/author/fw/releases/17/attach_files/29/download",
        )
    validate_release(release)
    monkeypatch.setattr(platforms, "HuggingFacePublishHubClient", no_remote)
    monkeypatch.setattr(platforms, "GiteeRepository", no_remote)
    requests = []

    def download(request):
        requests.append(request)
        assert request.url.host == ("gitee.com" if provider == "gitee" else "huggingface.co")
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, content=data)

    with httpx.Client(transport=httpx.MockTransport(download)) as client:
        # Keep release validation, anonymous transfer and hash/identity checks real.
        monkeypatch.setattr(platforms, "acquire_package", partial(acquire_package, client=client))
        with pytest.raises(FirmwareError, match="complete installation"):
            FirmwarePlatform(provider).submit(
                release, AccessToken("unused"), cache_root=tmp_path,
            )
    assert len(requests) == 1


def test_update_remains_available_for_local_preparation_and_inspection(tmp_path):
    source = package(tmp_path / "update.zip")
    result = prepare_package(source, tmp_path / "cache")
    assert result["mode"] == "application"
    assert inspect_installation(source, require_layout=True).mode == "application"
