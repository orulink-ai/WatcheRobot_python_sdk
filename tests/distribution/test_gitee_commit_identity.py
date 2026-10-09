"""Only exact commit objects may identify public application snapshots."""

import base64

import pytest

from watcherobot.distribution.gitee_repository import GiteeRepository
from watcherobot.distribution.ports import (
    AccessToken, HubInvalidResponse, HubNetworkError, HubRateLimitError,
    HubRevisionNotFound,
)
from watcherobot.distribution.submit import SubmitError, submit_application
from tests.distribution._publishing_fakes import (
    FakeCredentialStore, FakeIdentityHub, FakePublishHub, RecordingEvents,
    write_application,
)
from tests.distribution.test_gitee_repository import SHA, NEW_SHA, tree


class CommitApi:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = {"sha": SHA} if payload is None else payload
        self.calls = []

    def request(self, method, path, token, data=None):
        assert method == "GET" and token is None
        self.calls.append(path)
        if "/commits/" in path:
            return self.status, self.payload
        if "/git/trees/" in path:
            return 200, tree({"app.json": b"{}"})
        if "/git/blobs/" in path:
            return 200, {"encoding": "base64", "content": base64.b64encode(b"{}").decode()}
        raise AssertionError(path)


@pytest.mark.parametrize("operation", ["file", "snapshot"])
@pytest.mark.parametrize("status,payload,error", [
    (404, {}, HubRevisionNotFound),  # A tree exists, but there is no commit object.
    (200, {"sha": NEW_SHA}, HubInvalidResponse),
    (200, {}, HubInvalidResponse),
    (200, [], HubInvalidResponse),
    (429, {}, HubRateLimitError),
    (503, {}, HubNetworkError),
])
def test_invalid_commit_is_rejected_before_tree_read(tmp_path, operation, status, payload, error):
    api = CommitApi(status, payload)
    client = GiteeRepository(api=api)
    with pytest.raises(error):
        if operation == "file":
            client.read_repository_file(
                AccessToken("unused"), repo_id="owner/app", commit=SHA, path="app.json",
            )
        else:
            client.download_repository_snapshot(repo_id="owner/app", commit=SHA, target=tmp_path)
    assert api.calls == [f"repos/owner/app/commits/{SHA}"]
    assert not list(tmp_path.iterdir())


def test_verified_commit_cache_is_bounded_and_repository_scoped(tmp_path):
    api = CommitApi()
    client = GiteeRepository(api=api)
    for repo, commit in [("owner/app", SHA), ("owner/app", SHA),
                         ("other/app", SHA), ("other/app", NEW_SHA), ("owner/app", SHA)]:
        api.payload = {"sha": commit}
        assert client.read_repository_file(repo_id=repo, commit=commit, path="app.json") == b"{}"
    client.download_repository_snapshot(repo_id="owner/app", commit=SHA, target=tmp_path)
    assert (tmp_path / "app.json").read_bytes() == b"{}"
    assert len([path for path in api.calls if "/commits/" in path]) == 4


def test_failed_commit_verification_is_not_cached():
    api = CommitApi(payload={"sha": NEW_SHA})
    client = GiteeRepository(api=api)
    with pytest.raises(HubInvalidResponse):
        client.read_repository_file(repo_id="owner/app", commit=SHA, path="app.json")
    api.payload = {"sha": SHA}
    assert client.read_repository_file(repo_id="owner/app", commit=SHA, path="app.json") == b"{}"
    assert len([path for path in api.calls if "/commits/" in path]) == 2


def test_tree_sha_submission_stops_before_catalog_access(tmp_path):
    write_application(tmp_path)
    hub = FakePublishHub()
    hub.read_repository_file = GiteeRepository(api=CommitApi(404, {})).read_repository_file
    with pytest.raises(SubmitError, match="publish 返回的完整提交 SHA"):
        submit_application(
            tmp_path, provider="gitee", commit=SHA,
            credentials=FakeCredentialStore(AccessToken("test")),
            identity_hub=FakeIdentityHub(), publish_hub=hub,
            events=RecordingEvents(), watcherobot_version="0.1.1a3",
        )
    assert hub.calls == []
