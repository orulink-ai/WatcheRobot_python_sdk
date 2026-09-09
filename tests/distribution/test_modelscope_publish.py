from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from watcherobot.distribution.modelscope_publish import (
    ModelScopeHubClient,
    ModelScopePublishHubClient,
)
from watcherobot.distribution.ports import (
    AccessToken,
    HubIdentity,
    HubInvalidResponse,
    HubRepositoryConflict,
    UploadFile,
)


class FakeModelScopeApi:
    def __init__(self, *, exists: bool = True, corrupt_hash: bool = False) -> None:
        self.exists = exists
        self.corrupt_hash = corrupt_hash
        self.files: dict[str, bytes] = {"stale.py": b"old"}
        self.calls: list[tuple[str, object]] = []

    def whoami(self):
        return SimpleNamespace(username="developer", display_name="Developer")

    def get_repo(self, repo_id: str, repo_type: str):
        self.calls.append(("get_repo", (repo_id, repo_type)))
        if not self.exists:
            raise type("NotExistError", (RuntimeError,), {})()
        return SimpleNamespace(private=False, visibility="public")

    def create_repo(self, repo_id: str, repo_type: str, **kwargs):
        self.calls.append(("create_repo", (repo_id, repo_type, kwargs)))
        self.exists = True
        return SimpleNamespace(private=False, visibility="public")

    def upload_file(
        self,
        repo_id: str,
        repo_type: str,
        source: str | Path | bytes,
        path_in_repo: str,
        **kwargs,
    ) -> dict[str, object]:
        self.calls.append(("upload", path_in_repo))
        self.files[path_in_repo] = (
            Path(source).read_bytes() if isinstance(source, (str, Path)) else source
        )
        return {}

    def list_repo_files(self, repo_id: str, repo_type: str, **kwargs):
        result = []
        for path, content in self.files.items():
            digest = hashlib.sha256(content).hexdigest()
            if self.corrupt_hash and path == "app.py":
                digest = "0" * 64
            result.append(SimpleNamespace(path=path, sha256=digest))
        result.append(SimpleNamespace(path=".gitattributes", sha256="1" * 64))
        return result

    def delete_files(
        self,
        repo_id: str,
        repo_type: str,
        paths: list[str],
        **kwargs,
    ) -> dict[str, object]:
        self.calls.append(("delete", tuple(paths)))
        for path in paths:
            self.files.pop(path)
        return {}


def test_modelscope_identity_and_public_dataset_publish(tmp_path: Path) -> None:
    api = FakeModelScopeApi(exists=False)
    token = AccessToken("ms-secret")
    identity = ModelScopeHubClient(api_factory=lambda _token: api).whoami(token)
    client = ModelScopePublishHubClient(
        api_factory=lambda _token: api,
        ref_resolver=lambda _repo: "a" * 40,
    )
    source = tmp_path / "app.py"
    source.write_text("print('ok')\n", encoding="utf-8")

    repository = client.ensure_public_repository(
        token,
        repository_id="developer/WatcherRobot-com.example.demo",
    )
    client.replace_repository_files(
        token,
        repository_id=repository.repository_id,
        files=(
            UploadFile.from_path("app.py", source),
            UploadFile.from_bytes("app.json", b"{}\n"),
        ),
        commit_message="Publish demo",
    )
    revision = client.get_repository_head(
        token,
        repository_id=repository.repository_id,
    )

    assert identity == HubIdentity("developer", "Developer")
    assert repository.repository_type == "dataset"
    assert repository.created is True
    assert set(api.files) == {"app.py", "app.json"}
    assert ("delete", ("stale.py",)) in api.calls
    assert revision.commit == "a" * 40
    assert "modelscope.cn/datasets/" in revision.url


def test_modelscope_upload_fails_closed_when_remote_hash_differs() -> None:
    api = FakeModelScopeApi(corrupt_hash=True)
    client = ModelScopePublishHubClient(
        api_factory=lambda _token: api,
        ref_resolver=lambda _repo: "a" * 40,
    )

    with pytest.raises(HubInvalidResponse, match="verification failed"):
        client.replace_repository_files(
            AccessToken("ms-secret"),
            repository_id="developer/WatcherRobot-com.example.demo",
            files=(UploadFile.from_bytes("app.py", b"print('ok')\n"),),
            commit_message="Publish demo",
        )


def test_modelscope_rejects_internal_repository() -> None:
    api = FakeModelScopeApi()
    api.get_repo = lambda _repo_id, _repo_type: SimpleNamespace(
        private=False,
        visibility=3,
    )
    client = ModelScopePublishHubClient(api_factory=lambda _token: api)

    with pytest.raises(HubRepositoryConflict, match="not public"):
        client.ensure_public_repository(
            AccessToken("ms-secret"),
            repository_id="developer/WatcherRobot-com.example.demo",
        )
