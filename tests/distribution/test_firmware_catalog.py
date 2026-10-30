import json

import pytest

from watcherobot.firmware.catalog import parse_catalog, resolve_release
from watcherobot.firmware.contract import FirmwareError, FirmwareRequirement


def entry(**changes):
    return {
        "firmware_id": "com.example.demo",
        "version": "1.0.0",
        "repo_id": "author/firmware",
        "commit": "a" * 40,
        "path": "versions/1.0.0/firmware.zip",
        "size": 1024,
        "sha256": "b" * 64,
        "withdrawn": False,
        **changes,
    }


def parse(entries, provider="huggingface"):
    return parse_catalog(
        json.dumps({"schema_version": 1, "firmwares": entries}).encode(),
        provider=provider,
    )


def test_catalog_never_uses_latest_or_infers_official_identity():
    releases = parse([entry()])
    assert releases[0].provider == "huggingface"
    assert releases[0].commit == "a" * 40
    with pytest.raises(FirmwareError):
        parse([entry(commit="main")])
    with pytest.raises(FirmwareError):
        parse([entry(official=True)])


@pytest.mark.parametrize(
    "change",
    [
        {"path": "../firmware.zip"},
        {"size": True},
        {"sha256": "bad"},
        {"repo_id": "author/repo/escape"},
        {"version": "latest"},
    ],
)
def test_rejects_invalid_reference_before_network_access(change):
    with pytest.raises(FirmwareError):
        parse([entry(**change)])


def test_duplicate_identity_version_is_rejected_even_for_different_content():
    with pytest.raises(FirmwareError):
        parse([entry(), entry(sha256="c" * 64)])


def test_recommended_exact_release_is_resolved_in_same_provider_only():
    req = FirmwareRequirement.parse(
        {
            "mode": "required",
            "firmware_id": "com.example.demo",
            "version_spec": ">=1.0.0,<2.0.0",
            "recommended_version": "1.0.0",
            "tested_versions": ["1.0.0"],
        }
    )
    releases = parse([entry(), entry(version="1.1.0")])
    assert resolve_release(req, releases, provider="huggingface").version == "1.0.0"
    with pytest.raises(FirmwareError):
        resolve_release(req, releases, provider="gitee")
    with pytest.raises(FirmwareError):
        resolve_release(req, parse([entry(withdrawn=True)]), provider="huggingface")


def test_gitee_requires_release_asset_not_git_zip_or_arbitrary_url():
    gitee = {k: v for k, v in entry().items() if k != "path"}
    gitee.update(
        asset_id=123,
        download_url="https://gitee.com/author/firmware/releases/download/v1.0.0/firmware.zip",
    )
    assert parse([gitee], "gitee")[0].asset_id == 123
    gitee["download_url"] = "https://evil.example/firmware.zip"
    with pytest.raises(FirmwareError):
        parse([gitee], "gitee")
