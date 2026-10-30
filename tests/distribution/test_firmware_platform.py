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
from tests.distribution.test_firmware_contract import make_package


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
    from tests.distribution.test_firmware_transfer import artifact
    from tests.distribution._publishing_fakes import FakePublishHub
    from watcherobot.distribution.ports import CatalogDocument

    release, _ = artifact(tmp_path)
    hub = FakePublishHub(
        catalog=CatalogDocument(
            commit="a" * 40, content=b'{"schema_version":1,"firmwares":[]}'
        )
    )
    monkeypatch.setattr(platforms, "HuggingFacePublishHubClient", lambda: hub)
    monkeypatch.setattr(platforms, "acquire_package", lambda *a, **kw: None)
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
def test_reviewed_metadata_is_preserved_on_publication_resubmission(tmp_path, monkeypatch, withdrawn):
    from dataclasses import replace
    from watcherobot.firmware import platforms
    from watcherobot.firmware.transfer import release_record
    from tests.distribution.test_firmware_transfer import artifact
    from tests.distribution._publishing_fakes import FakePublishHub
    from watcherobot.distribution.ports import CatalogDocument

    release, _ = artifact(tmp_path)
    reviewed = replace(release, notes="Maintainer-reviewed notes", publisher_kind="official",
                       ota_supported=True, withdrawn=withdrawn)
    hub = FakePublishHub(catalog=CatalogDocument(commit="a" * 40, content=json.dumps({
        "schema_version": 1, "firmwares": [release_record(reviewed)],
    }).encode()))
    monkeypatch.setattr(platforms, "HuggingFacePublishHubClient", lambda: hub)
    monkeypatch.setattr(platforms, "acquire_package", lambda *a, **kw: None)
    backend = FirmwarePlatform("huggingface")
    if withdrawn:
        with pytest.raises(FirmwareError, match="withdrawn"):
            backend.submit(release, AccessToken("secret"), cache_root=tmp_path)
    else:
        assert backend.submit(release, AccessToken("secret"), cache_root=tmp_path)["status"] == "already_listed"
    assert not any(name == "create_pr" for name, _ in hub.calls)
