import hashlib
from dataclasses import replace

import httpx
import pytest

from watcherobot.firmware.catalog import FirmwareRelease
from watcherobot.firmware.contract import FirmwareError
from watcherobot.firmware.transfer import acquire_package
from tests.distribution.test_firmware_contract import make_package


def artifact(tmp_path):
    path = tmp_path / "source.zip"
    make_package(path)
    data = path.read_bytes()
    release = FirmwareRelease(
        "huggingface",
        "com.example.firmware",
        "1.0.0",
        "author/fw",
        "a" * 40,
        len(data),
        hashlib.sha256(data).hexdigest(),
        False,
        "versions/1.0.0/firmware.zip",
    )
    return release, data


@pytest.mark.parametrize("provider", ["huggingface", "gitee"])
def test_verified_cache_and_real_download_progress(tmp_path, provider):
    release, data = artifact(tmp_path)
    if provider == "gitee":
        release = replace(
            release,
            provider="gitee",
            path="",
            asset_id=29,
            download_url="https://gitee.com/api/v5/repos/author/fw/releases/17/attach_files/29/download",
        )
    requests, progress = [], []

    def respond(request):
        requests.append(request)
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, content=data)

    with httpx.Client(
        transport=httpx.MockTransport(respond),
        headers={"Authorization": "Bearer must-not-leak", "Cookie": "must-not-leak"},
    ) as client:
        path = acquire_package(
            release,
            tmp_path / "cache",
            client=client,
            progress=lambda **e: progress.append(e),
        )
        assert path.read_bytes() == data
        assert progress[-1]["downloaded_bytes"] == len(data)
        assert acquire_package(release, tmp_path / "cache", client=client) == path
        assert len(requests) == 1


def test_rejects_wrong_identity_despite_valid_digest(tmp_path):
    release, data = artifact(tmp_path)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=data))
    ) as client:
        with pytest.raises(FirmwareError):
            acquire_package(
                replace(release, firmware_id="com.other"),
                tmp_path / "cache",
                client=client,
            )
        assert not list((tmp_path / "cache").glob("*.zip"))


def test_corrupt_transfer_and_untrusted_redirect_cannot_publish_cache(tmp_path):
    release, data = artifact(tmp_path)
    for response in (
        httpx.Response(200, content=b"bad"),
        httpx.Response(302, headers={"location": "http://127.0.0.1/fw.zip"}),
    ):
        with httpx.Client(transport=httpx.MockTransport(lambda r: response)) as client:
            with pytest.raises(FirmwareError):
                acquire_package(release, tmp_path / "cache", client=client)
    assert not list((tmp_path / "cache").glob("*.zip"))


def test_withdrawn_release_does_not_use_existing_cache(tmp_path):
    release, _ = artifact(tmp_path)
    with pytest.raises(FirmwareError):
        acquire_package(replace(release, withdrawn=True), tmp_path / "cache")
