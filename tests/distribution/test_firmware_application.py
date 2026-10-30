import json
from pathlib import Path

import pytest

from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
    parse_application_manifest,
)


def document(schema=3):
    return {
        "schema_version": schema,
        "id": "com.example.app",
        "name": "Demo",
        "version": "1.0.0",
        "requires_sdk": ">=0.1",
        "requires_daemon": {"application_protocol": ">=1,<2"},
        "dependencies": [],
        "supported_host_platforms": ["windows", "macos"],
        "requires_firmware": {
            "mode": "required",
            "firmware_id": "com.example.firmware",
            "version_spec": ">=1.0.0,<2.0.0",
            "recommended_version": "1.0.0",
            "tested_versions": ["1.0.0"],
        },
    }


def test_new_application_preserves_dependency_roundtrip():
    value = document()
    parsed = parse_application_manifest(json.dumps(value).encode())
    assert parsed.requires_firmware.to_dict() == value["requires_firmware"]
    assert parsed.to_dict()["requires_firmware"] == value["requires_firmware"]


def test_existing_schema_can_declare_optional_firmware_requirement():
    parsed = parse_application_manifest(json.dumps(document(3)).encode())
    assert parsed.requires_firmware.firmware_id == "com.example.firmware"


def test_missing_optional_requirement_keeps_existing_application_usable():
    value = document()
    del value["requires_firmware"]
    assert (
        parse_application_manifest(json.dumps(value).encode()).requires_firmware is None
    )


@pytest.mark.parametrize("invalid", [None, {}, [], "required"])
def test_present_invalid_firmware_requirement_is_not_legacy_unknown(invalid):
    value = document()
    value["requires_firmware"] = invalid
    with pytest.raises(ApplicationManifestError):
        parse_application_manifest(json.dumps(value).encode())


@pytest.mark.parametrize("provider", ["huggingface", "gitee"])
def test_existing_and_new_applications_share_one_catalog(provider):
    from tests.distribution.test_marketplace import (
        FakeMarketplaceHub,
        _catalog,
        FIRST_SPACE,
        FIRST_COMMIT,
    )
    from watcherobot.distribution.marketplace import (
        load_official_marketplace,
    )

    value = document()
    value.update(
        id="com.example.first", description="Demo", author="Developer", icon=""
    )
    hub = FakeMarketplaceHub(
        catalog=_catalog([{"repo_id": FIRST_SPACE, "commit": FIRST_COMMIT}]),
        manifests={(FIRST_SPACE, FIRST_COMMIT): json.dumps(value).encode()},
    )
    result = load_official_marketplace(provider=provider, hub=hub)
    assert result.applications[0].requires_firmware == value["requires_firmware"]


@pytest.mark.parametrize("required", [False, True])
def test_submission_uses_the_existing_application_catalog(
    tmp_path, monkeypatch, required
):
    from tests.distribution.test_install import _write_source_v3
    from tests.distribution._publishing_fakes import FakePublishHub
    from tests.distribution.test_submit import _submit
    from watcherobot.firmware.platforms import FirmwarePlatform

    project = tmp_path / "project"
    _write_source_v3(project)
    path = project / "app.json"
    value = json.loads(path.read_bytes())
    value.update(
        schema_version=3,
        description="Demo",
        author="Developer",
        icon="",
        requires_firmware={"mode": "not_required"},
    )
    if required:
        value["requires_firmware"] = document()["requires_firmware"]
    path.write_text(json.dumps(value))
    hub = FakePublishHub(remote_manifest=path.read_bytes())
    monkeypatch.setattr(FirmwarePlatform, "catalog", lambda self: ("a" * 40, ()))
    if required:
        with pytest.raises(ApplicationManifestError, match="reviewed"):
            _submit(project, hub)
        assert not hub.calls
    else:
        _submit(project, hub)
        writes = [args for name, args in hub.calls if name == "create_pr"]
        assert writes[0][1] == "app-list.json"


@pytest.mark.parametrize("provider", ["huggingface", "gitee"])
def test_firmware_dependency_keeps_exact_sdk_lock_and_inventory_requirement(tmp_path, provider):
    from tests.distribution.test_install import (
        _write_source_v3,
        _write_runtime,
        FakeHub,
        FakeEnvironmentRunner,
        SPACE_ID,
        COMMIT,
    )
    from watcherobot.distribution.install import (
        install_application,
        list_installed_applications,
    )

    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    _write_source_v3(source)
    _write_runtime(runtime)
    path = source / "app.json"
    value = json.loads(path.read_bytes())
    value.update(requires_firmware=document()["requires_firmware"])
    path.write_text(json.dumps(value))
    runner = FakeEnvironmentRunner()
    install_application(
        provider=provider,
        repo_id=SPACE_ID,
        commit=COMMIT,
        store_root=tmp_path / "store",
        runtime_root=runtime,
        hub=FakeHub(source),
        environment_runner=runner,
    )
    installed = list_installed_applications(tmp_path / "store")[0]
    assert installed.status == "installed"
    assert installed.to_dict()["requires_firmware"] == value["requires_firmware"]
    command = next(
        item for item in runner.commands if item.stage == "installing_dependencies"
    )
    assert "--no-deps" in command.arguments
    assert "watcherobot==0.1.1a3" in command.arguments

    # Compose installation with real dependency resolution and anonymous transfer.
    # A failed firmware download preserves the installed application for retry.
    import httpx
    from dataclasses import replace
    from watcherobot.firmware import operations
    from watcherobot.firmware.contract import FirmwareError
    from watcherobot.firmware.transfer import acquire_package, release_record
    from tests.distribution.test_firmware_transfer import artifact
    from unittest.mock import patch

    release, firmware_bytes = artifact(tmp_path)
    if provider == "gitee":
        release = replace(release, provider=provider, path="", asset_id=29,
                          download_url="https://gitee.com/api/v5/repos/author/fw/releases/17/attach_files/29/download")
    cache = tmp_path / "firmware-cache"
    operations._write(cache / provider / "catalog.json", {
        "schema_version": 1, "firmwares": [release_record(release)],
    })
    requests = []

    def respond(request):
        requests.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(503) if len(requests) == 1 else httpx.Response(200, content=firmware_bytes)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        def download(release, root, progress, **kwargs):
            return acquire_package(release, root, client=client, progress=progress, **kwargs)

        with patch.object(operations.FirmwarePlatform, "catalog", return_value=("a" * 40, (release,))), patch.object(operations, "acquire_package", side_effect=download):
            def prepare():
                return operations.execute("download", {"app_id": value["id"]}, provider=provider,
                                          cache_root=cache, store_root=tmp_path / "store", progress=lambda **_: None)

            with pytest.raises(FirmwareError, match="503"):
                prepare()
            assert list_installed_applications(tmp_path / "store")[0].status == "installed"
            result = prepare()
            assert Path(result["package_path"]).read_bytes() == firmware_bytes
            assert result["release"]["firmware_id"] == value["requires_firmware"]["firmware_id"]
            assert result["release"]["provider"] == provider
            prepare()
            assert len(requests) == 2, "retry and subsequent preparation reuse only verified bytes"
