import base64
import hashlib
from types import SimpleNamespace

import pytest

from watcherobot.distribution.gitee_repository import GiteeRepository
from watcherobot.distribution.ports import (
    AccessToken,
    HubCatalogConflict,
    HubInvalidResponse,
    HubRepositoryConflict,
    UploadFile,
)

SHA = "a" * 40
NEW_SHA = "b" * 40


def digest(content: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(content)}\0".encode() + content,
        usedforsecurity=False,
    ).hexdigest()


def tree(files: dict[str, bytes]):
    return {
        "tree": [
            {"path": path, "type": "blob", "mode": "100644", "size": len(content), "sha": digest(content)}
            for path, content in files.items()
        ],
        "truncated": False,
    }


class PublishApi:
    def __init__(self, initial: dict[str, bytes], final: dict[str, bytes] | None = None):
        self.initial = initial
        self.final = final
        self.calls = []

    def request(self, method, path, token, data=None):
        self.calls.append((method, path, token, data))
        if path == "repos/alice/app":
            return 200, {"private": False, "default_branch": "master"}
        if path == "repos/alice/app/branches/master":
            return 200, {"commit": {"sha": SHA}}
        if path == f"repos/alice/app/git/trees/{SHA}?recursive=1":
            return 200, tree(self.initial)
        if method == "POST" and path == "repos/alice/app/commits":
            return 201, {"sha": NEW_SHA}
        if path == f"repos/alice/app/git/trees/{NEW_SHA}?recursive=1":
            return 200, tree(self.final if self.final is not None else self.initial)
        raise AssertionError((method, path))


def test_publish_uses_one_api_commit_with_base64_create_update_delete():
    desired = {"app.json": b"{}", "资源/asset.bin": bytes(range(256))}
    api = PublishApi(
        {"app.json": b"old", "obsolete.txt": b"remove"},
        desired,
    )

    GiteeRepository(api=api).replace_repository_files(
        AccessToken("unit-test-secret"),
        repo_id="alice/app",
        files=tuple(UploadFile.from_bytes(path, content) for path, content in desired.items()),
        commit_message="publish",
    )

    writes = [call for call in api.calls if call[0] == "POST"]
    assert len(writes) == 1
    payload = writes[0][3]
    assert payload["branch"] == "master"
    assert payload["message"] == "publish"
    actions = {item["path"]: item for item in payload["actions"]}
    assert actions["app.json"]["action"] == "update"
    assert actions["obsolete.txt"]["action"] == "delete"
    assert actions["资源/asset.bin"]["action"] == "create"
    assert base64.b64decode(actions["资源/asset.bin"]["content"]) == desired["资源/asset.bin"]
    assert all(item.get("encoding") == "base64" for item in actions.values() if item["action"] != "delete")
    assert "unit-test-secret" not in repr(api.calls)


def test_publish_skips_commit_when_snapshot_is_unchanged():
    files = {"app.json": b"{}"}
    api = PublishApi(files)
    GiteeRepository(api=api).replace_repository_files(
        AccessToken("test"), repo_id="alice/app",
        files=(UploadFile.from_bytes("app.json", b"{}"),), commit_message="publish",
    )
    assert not [call for call in api.calls if call[0] == "POST"]


def test_publish_verifies_resulting_tree():
    api = PublishApi({}, {"app.json": b"wrong"})
    with pytest.raises(HubInvalidResponse, match="differs"):
        GiteeRepository(api=api).replace_repository_files(
            AccessToken("test"), repo_id="alice/app",
            files=(UploadFile.from_bytes("app.json", b"{}"),), commit_message="publish",
        )


def test_publish_rejects_unsafe_or_oversized_files_before_api():
    class NoApi:
        def request(self, *args, **kwargs):
            pytest.fail("invalid snapshot must fail before network access")

    repository = GiteeRepository(api=NoApi())
    with pytest.raises(HubInvalidResponse):
        repository.replace_repository_files(
            AccessToken("test"), repo_id="alice/app",
            files=(UploadFile.from_bytes(".git/config", b"unsafe"),), commit_message="publish",
        )
    with pytest.raises(HubInvalidResponse):
        repository.replace_repository_files(
            AccessToken("test"), repo_id="alice/app",
            files=(UploadFile.from_bytes("asset.bin", b"x" * (100 * 1024 * 1024 + 1)),),
            commit_message="publish",
        )


def test_private_existing_repository_is_not_made_public():
    class Api:
        def __init__(self):
            self.calls = []
        def request(self, method, path, token, data=None):
            self.calls.append((method, path, token, data))
            return 200, {"private": True}

    api = Api()
    identity = SimpleNamespace(whoami=lambda token: SimpleNamespace(username="alice"))
    with pytest.raises(HubRepositoryConflict):
        GiteeRepository(api=api, identity=identity).ensure_public_repository(
            AccessToken("test"), repo_id="alice/app", sdk="static"
        )
    assert [call[0] for call in api.calls] == ["GET"]


def test_submission_writes_only_fork_branch_then_creates_pr(monkeypatch):
    class Api:
        def __init__(self):
            self.calls = []
        def request(self, method, path, token, data=None):
            self.calls.append((method, path, token, data))
            if path == "repos/alice/catalog":
                return 200, {"parent": {"full_name": "team/catalog"}}
            if method == "POST" and path == "repos/alice/catalog/commits":
                return 201, {"sha": NEW_SHA}
            if path == "repos/team/catalog":
                return 200, {"default_branch": "master"}
            if method == "POST" and path == "repos/team/catalog/pulls":
                return 201, {"number": 3, "title": "review", "html_url": "https://gitee.com/team/catalog/pulls/3"}
            raise AssertionError((method, path))

    api = Api()
    identity = SimpleNamespace(whoami=lambda token: SimpleNamespace(username="alice"))
    hub = GiteeRepository(api=api, identity=identity)
    hub.get_repository_head = lambda *a, **kw: SimpleNamespace(commit=SHA)
    monkeypatch.setattr("watcherobot.distribution.gitee_repository.uuid.uuid4", lambda: SimpleNamespace(hex="branch"))

    result = hub.create_catalog_pull_request(
        AccessToken("test"), repo_id="team/catalog", path="app-list.json",
        content=b"[]", parent_commit=SHA, title="review", description="manual",
    )

    assert result.number == 3
    commit_call = next(call for call in api.calls if call[1] == "repos/alice/catalog/commits")
    assert commit_call[3]["start_branch"] == SHA
    assert commit_call[3]["actions"][0]["content"] == base64.b64encode(b"[]").decode()
    pr_call = next(call for call in api.calls if call[1] == "repos/team/catalog/pulls")
    assert pr_call[3]["head"] == "alice/catalog:watcher-submit-branch"


def test_wrong_fork_never_receives_writes():
    class Api:
        def __init__(self): self.calls = []
        def request(self, method, path, token, data=None):
            self.calls.append((method, path, token, data))
            return 200, {"parent": {"full_name": "other/catalog"}}

    api = Api()
    hub = GiteeRepository(
        api=api,
        identity=SimpleNamespace(whoami=lambda token: SimpleNamespace(username="alice")),
    )
    hub.get_repository_head = lambda *a, **kw: SimpleNamespace(commit=SHA)
    with pytest.raises(HubRepositoryConflict):
        hub.create_catalog_pull_request(
            AccessToken("test"), repo_id="team/catalog", path="app-list.json",
            content=b"[]", parent_commit=SHA, title="review", description="manual",
        )
    assert all(call[0] == "GET" for call in api.calls)


def test_stale_catalog_is_rejected_before_fork_write():
    hub = GiteeRepository()
    hub.get_repository_head = lambda *a, **kw: SimpleNamespace(commit=NEW_SHA)
    with pytest.raises(HubCatalogConflict):
        hub.create_catalog_pull_request(
            AccessToken("test"), repo_id="team/catalog", path="app-list.json",
            content=b"[]", parent_commit=SHA, title="review", description="manual",
        )
