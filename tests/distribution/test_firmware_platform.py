from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict
import json
import base64
import httpx

import pytest

from watcherobot.distribution.ports import AccessToken
from watcherobot.firmware.contract import FirmwareError
from watcherobot.firmware.platforms import FirmwarePlatform
from watcherobot.firmware.package import inspect_package
from tests.distribution.test_firmware_installation import package as make_package


class Hf:
    def __init__(self):
        self.calls = []

    def whoami(self):
        return {"name": "author"}

    def create_repo(self, **kwargs):
        self.calls.append(("create", kwargs))

    def repo_info(self, **kwargs):
        self.calls.append(("info", kwargs))
        return SimpleNamespace(sha="a" * 40)

    def get_paths_info(self, **kwargs):
        return []

    def create_commit(self, **kwargs):
        self.calls.append(("commit", kwargs))
        return SimpleNamespace(oid="b" * 40)


def test_hf_firmware_is_append_only_dataset_not_application_space(tmp_path):
    package = tmp_path / "fw.zip"
    make_package(package)
    api = Hf()
    backend = FirmwarePlatform("huggingface", hf_factory=lambda token: api)
    release = backend.publish(package, AccessToken("secret"))
    assert release.repo_id == "author/WatcherRobot-Firmware-com.example.firmware"
    assert release.commit == "b" * 40
    assert all(call[1]["repo_type"] == "dataset" for call in api.calls)
    commit = next(call[1] for call in api.calls if call[0] == "commit")
    assert len(commit["operations"]) == 2
    assert commit["parent_commit"] == "a" * 40
    assert all(type(op).__name__ == "CommitOperationAdd" for op in commit["operations"])


def test_hf_existing_version_is_never_overwritten(tmp_path):
    package = tmp_path / "fw.zip"
    make_package(package)
    api = Hf()
    api.get_paths_info = lambda **kwargs: [
        SimpleNamespace(path="existing", last_commit=SimpleNamespace(oid="a" * 40))
    ]
    with pytest.raises(FirmwareError):
        FirmwarePlatform("huggingface", hf_factory=lambda token: api).publish(
            package, AccessToken("secret")
        )
    assert not any(call[0] == "commit" for call in api.calls)


def test_hf_retries_same_content_without_reupload(tmp_path):
    package = tmp_path / "fw.zip"
    make_package(package)
    metadata = tmp_path / "release.json"
    metadata.write_text(json.dumps(asdict(inspect_package(package))))
    api = Hf()
    api.get_paths_info = lambda **kwargs: [
        SimpleNamespace(path="existing", last_commit=SimpleNamespace(oid="a" * 40))
    ]
    api.hf_hub_download = lambda **kwargs: str(metadata)
    release = FirmwarePlatform("huggingface", hf_factory=lambda token: api).publish(
        package, AccessToken("secret")
    )
    assert release.commit == "a" * 40
    assert not any(call[0] == "commit" for call in api.calls)


@pytest.mark.parametrize("provider", ["huggingface", "gitee"])
@pytest.mark.parametrize("metadata_case, accepted", [
    ("legacy_missing_empty", True), ("legacy_explicit_empty", True),
    ("different_hash", False), ("null_table", False),
    ("different_table", False), ("missing_required_table", False),
])
def test_retry_preserves_legacy_metadata_compatibility(tmp_path, provider, metadata_case, accepted):
    from zipfile import ZipFile
    from watcherobot.firmware.installation import inspect_installation

    path = make_package(tmp_path / "legacy-full.zip", full=True)
    if metadata_case != "missing_required_table":
        with ZipFile(path) as archive:
            files = {entry.filename: archive.read(entry) for entry in archive.infolist()}
        manifest = json.loads(files["firmware.json"])
        manifest["schema_version"] = 1
        manifest.pop("partition_table")
        files["firmware.json"] = json.dumps(manifest).encode()
        with ZipFile(path, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
    assert inspect_installation(path, require_layout=True).mode == "full"
    package = inspect_package(path)
    saved = asdict(package)
    if metadata_case in {"legacy_missing_empty", "missing_required_table"}:
        saved.pop("partition_table")
    elif metadata_case == "different_hash":
        saved["sha256"] = "0" * 64
    elif metadata_case == "null_table":
        saved["partition_table"] = None
    elif metadata_case == "different_table":
        saved["partition_table"] = "another-table.bin"
    metadata = tmp_path / "saved-release.json"
    metadata.write_text(json.dumps(saved))
    original_metadata = metadata.read_bytes()
    class HistoricalHf(Hf):
        def get_paths_info(self, **kwargs):
            return [SimpleNamespace(last_commit=SimpleNamespace(oid="a" * 40))]

        def hf_hub_download(self, **kwargs):
            return str(metadata)

    api = HistoricalHf()
    calls = []
    def response(request):
        calls.append((request.method, request.url.path))
        suffix = request.url.path.removeprefix("/api/v5/")
        if suffix == "user":
            value = {"login": "author"}
        elif suffix.endswith("/releases/tags/v1.0.0"):
            return httpx.Response(404)
        elif "/contents/" in suffix:
            assert request.method == "GET", "Existing metadata must not be overwritten"
            value = {"content": base64.b64encode(metadata.read_bytes()).decode()}
        elif suffix.endswith("/commits"):
            value = [{"sha": "a" * 40}]
        elif suffix.endswith("/releases"):
            value = {"id": 17}
        elif suffix.endswith("/attach_files"):
            assert request.method == "GET", "Existing asset must not be reuploaded"
            value = [{"id": 29, "name": "firmware.zip", "size": package.size}]
        else:
            value = {"private": False, "full_name": "author/WatcherRobot-Firmware-com.example.firmware",
                     "default_branch": "master"}
        return httpx.Response(200, json=value)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        backend = FirmwarePlatform(provider, hf_factory=lambda _: api, client=client)
        if accepted:
            release = backend.publish(path, AccessToken("secret"))
            assert release.sha256 == package.sha256
            assert release.commit == "a" * 40
        else:
            with pytest.raises(FirmwareError, match="metadata|different"):
                backend.publish(path, AccessToken("secret"))
            assert not any(method != "GET" for method, _ in calls)
    assert not any(name == "commit" for name, _ in api.calls)
    assert metadata.read_bytes() == original_metadata


@pytest.mark.parametrize("retry_metadata", [False, True])
@pytest.mark.parametrize("missing_status", [404, 200])
def test_gitee_publishes_release_attachment_without_replacing_old_files(
    tmp_path, retry_metadata, missing_status
):
    package = tmp_path / "fw.zip"
    make_package(package)
    calls = []
    repo = "author/WatcherRobot-Firmware-com.example.firmware"

    def response(request):
        calls.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer secret"
        assert "secret" not in str(request.url)
        suffix = request.url.path.removeprefix("/api/v5/")
        if suffix == "user":
            value = {"login": "author"}
        elif suffix == "repos/" + repo:
            value = {"private": False, "full_name": repo, "default_branch": "master"}
        elif "/releases/tags/" in suffix:
            return httpx.Response(404)
        elif "/contents/" in suffix:
            if request.method == "GET":
                if not retry_metadata:
                    return httpx.Response(missing_status, json=[])
                return httpx.Response(
                    200,
                    json={
                        "content": base64.b64encode(
                            json.dumps(asdict(inspect_package(package))).encode()
                        ).decode()
                    },
                )
            value = {"commit": {"sha": "a" * 40}}
        elif suffix.endswith("/commits"):
            value = [{"sha": "a" * 40}]
        elif suffix.endswith("/releases"):
            value = {"id": 17}
        elif suffix.endswith("/attach_files") and request.method == "GET":
            value = []
        elif suffix.endswith("/attach_files"):
            assert b'filename="firmware.zip"' in request.read()
            value = {"id": 29, "name": "firmware.zip", "size": package.stat().st_size}
        else:
            raise AssertionError(suffix)
        return httpx.Response(201 if request.method == "POST" else 200, json=value)

    progress = []
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        release = FirmwarePlatform("gitee", client=client).publish(
            package, AccessToken("secret"), progress=lambda **e: progress.append(e)
        )
    assert release.asset_id == 29
    assert release.download_url.endswith("/releases/17/attach_files/29/download")
    assert any(e.get("processed_bytes") == package.stat().st_size for e in progress)
    assert all(method != "DELETE" for method, _ in calls)


def test_gitee_retry_reuses_published_release_and_asset_without_mutation(tmp_path):
    package = tmp_path / "fw.zip"
    make_package(package)
    metadata = inspect_package(package)
    repo = "author/WatcherRobot-Firmware-com.example.firmware"
    calls = []

    def response(request):
        calls.append(request.method)
        assert request.method == "GET"
        if request.url.path.endswith("/user"):
            value = {"login": "author"}
        elif request.url.path.endswith(repo):
            value = {"private": False, "full_name": repo, "default_branch": "master"}
        elif "/releases/tags/" in request.url.path:
            value = {
                "id": 17,
                "body": "WatcherRobot firmware SHA256: " + metadata.sha256,
                "target_commitish": "a" * 40,
            }
        else:
            value = [{"id": 29, "name": "firmware.zip", "size": metadata.size}]
        return httpx.Response(200, json=value)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        release = FirmwarePlatform("gitee", client=client).publish(
            package, AccessToken("secret")
        )
    assert release.asset_id == 29
    assert len(calls) == 4


def test_firmware_submission_only_changes_separate_catalog_and_detects_version_conflict(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from watcherobot.firmware import platforms
    from watcherobot.firmware.transfer import release_record
    from tests.distribution.test_firmware_installation import (
        release_artifact as artifact,
    )
    from tests.distribution._publishing_fakes import FakePublishHub
    from watcherobot.distribution.ports import CatalogDocument

    release, _ = artifact(tmp_path)
    hub = FakePublishHub(
        catalog=CatalogDocument(
            commit="a" * 40, content=b'{"schema_version":1,"firmwares":[]}'
        )
    )
    monkeypatch.setattr(platforms, "HuggingFacePublishHubClient", lambda: hub)
    monkeypatch.setattr(
        platforms, "acquire_package", lambda *a, **kw: tmp_path / "source.zip"
    )
    backend = FirmwarePlatform("huggingface", hf_factory=lambda token: Hf())
    assert (
        backend.submit(release, AccessToken("secret"), cache_root=tmp_path)["status"]
        == "pending"
    )
    writes = [args for name, args in hub.calls if name == "create_pr"]
    assert writes[0][1] == "firmware-list.json"
    assert json.loads(writes[0][2])["firmwares"][0]["sha256"] == release.sha256
    hub.calls.clear()
    hub.catalog = CatalogDocument(
        commit="a" * 40,
        content=json.dumps(
            {"schema_version": 1, "firmwares": [release_record(release)]}
        ).encode(),
    )
    assert (
        backend.submit(release, AccessToken("secret"), cache_root=tmp_path)["status"]
        == "already_listed"
    )
    with pytest.raises(FirmwareError, match="different immutable"):
        backend.submit(
            replace(release, version="v1.0.0", commit="b" * 40),
            AccessToken("secret"),
            cache_root=tmp_path,
        )
    assert not any(name == "create_pr" for name, _ in hub.calls)


@pytest.mark.parametrize("withdrawn", [False, True])
def test_reviewed_metadata_is_preserved_on_publication_resubmission(
    tmp_path, monkeypatch, withdrawn
):
    from dataclasses import replace
    from watcherobot.firmware import platforms
    from watcherobot.firmware.transfer import release_record
    from tests.distribution.test_firmware_installation import (
        release_artifact as artifact,
    )
    from tests.distribution._publishing_fakes import FakePublishHub
    from watcherobot.distribution.ports import CatalogDocument

    release, _ = artifact(tmp_path)
    reviewed = replace(
        release,
        notes="Maintainer-reviewed notes",
        publisher_kind="official",
        ota_supported=True,
        withdrawn=withdrawn,
    )
    hub = FakePublishHub(
        catalog=CatalogDocument(
            commit="a" * 40,
            content=json.dumps(
                {
                    "schema_version": 1,
                    "firmwares": [release_record(reviewed)],
                }
            ).encode(),
        )
    )
    monkeypatch.setattr(platforms, "HuggingFacePublishHubClient", lambda: hub)
    monkeypatch.setattr(
        platforms, "acquire_package", lambda *a, **kw: tmp_path / "source.zip"
    )
    backend = FirmwarePlatform("huggingface")
    if withdrawn:
        with pytest.raises(FirmwareError, match="withdrawn"):
            backend.submit(release, AccessToken("secret"), cache_root=tmp_path)
    else:
        assert (
            backend.submit(release, AccessToken("secret"), cache_root=tmp_path)[
                "status"
            ]
            == "already_listed"
        )
    assert not any(name == "create_pr" for name, _ in hub.calls)


@pytest.mark.parametrize("provider", ["huggingface", "gitee"])
def test_direct_publish_accepts_wrapped_build_zip_and_uploads_prepared_segments(
    tmp_path, provider
):
    from zipfile import ZipFile
    from tests.distribution.test_firmware_preparation import build
    from watcherobot.firmware.preparation import prepare_package

    source = build(tmp_path / "build")
    raw = tmp_path / "官方构建.zip"
    with ZipFile(raw, "w") as archive:
        for path in source.iterdir():
            archive.write(path, "release/" + path.name)
    expected = prepare_package(raw, tmp_path / "expected")
    calls = []
    api = Hf()

    def response(request):
        calls.append(request.url.path)
        suffix = request.url.path.removeprefix("/api/v5/")
        if suffix == "user":
            value = {"login": "author"}
        elif (
            "/releases/tags/" in suffix
            or "/contents/" in suffix
            and request.method == "GET"
        ):
            return httpx.Response(404)
        elif "/contents/" in suffix:
            value = {"commit": {"sha": "a" * 40}}
        elif suffix.endswith("/commits"):
            value = [{"sha": "a" * 40}]
        elif suffix.endswith("/releases"):
            value = {"id": 17}
        elif suffix.endswith("/attach_files"):
            if request.method == "GET":
                value = []
            else:
                assert Path(expected["package_path"]).read_bytes() in request.read()
                value = {"id": 29, "name": "firmware.zip", "size": expected["size"]}
        else:
            value = {
                "private": False,
                "full_name": "author/WatcherRobot-Firmware-local.desktop_pet",
                "default_branch": "master",
            }
        return httpx.Response(201 if request.method == "POST" else 200, json=value)

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        release = FirmwarePlatform(
            provider, hf_factory=lambda _: api, client=client
        ).publish(raw, AccessToken("test-token"))
    assert release.sha256 == expected["sha256"]
    assert release.firmware_id == "local.desktop_pet"
    assert release.version == "0.3.3"
    assert [item["offset"] for item in expected["segments"]] == [0, 0x8000, 0x20000]
    assert api.calls if provider == "huggingface" else calls


@pytest.mark.parametrize("provider", ["huggingface", "gitee"])
@pytest.mark.parametrize("damage", ["overlap", "wrong_partition", "corrupt_image"])
def test_direct_build_upload_rejects_invalid_segments_before_provider_access(
    tmp_path, provider, damage
):
    from zipfile import ZipFile
    from tests.distribution.test_firmware_preparation import build

    source = build(tmp_path / "build")
    args = source / "flasher_args.json"
    metadata = json.loads(args.read_text())
    if damage == "overlap":
        metadata["flash_files"]["0x0"] = "app.bin"
        metadata["flash_files"]["0x1000"] = "bootloader.bin"
        (source / "app.bin").write_bytes(
            (source / "app.bin").read_bytes() + bytes(8192)
        )
    elif damage == "wrong_partition":
        metadata["flash_files"]["0x10000"] = metadata["flash_files"].pop("0x20000")
    else:
        (source / "app.bin").write_bytes(bytes(24))
    args.write_text(json.dumps(metadata))
    raw = tmp_path / "bad-build.zip"
    with ZipFile(raw, "w") as archive:
        for path in source.iterdir():
            archive.write(path, "release/" + path.name)

    def remote(*a, **kw):
        pytest.fail("Invalid segments must be rejected before provider access")

    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        with pytest.raises(FirmwareError, match={"overlap": "Overlapping", "wrong_partition": "write address", "corrupt_image": "image"}[damage]):
            FirmwarePlatform(provider, hf_factory=remote, client=client).publish(
                raw, AccessToken("unused")
            )
