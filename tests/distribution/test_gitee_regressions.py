"""Regressions for fixed-revision reads, file guards, and submission failures."""

import base64
import random
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from watcherobot.distribution.check import check_application
from watcherobot.distribution.download import MAX_SNAPSHOT_BYTES
from watcherobot.distribution.events import ErrorCode
from watcherobot.distribution.gitee_public import GiteePublicRepository
from watcherobot.distribution.gitee_repository import GiteeRepository
from watcherobot.distribution.hub_http import JsonResponse
from watcherobot.distribution.ports import (
    AccessToken, HubInvalidResponse, HubNetworkError, HubRateLimitError, UploadFile,
)
from watcherobot.distribution.submit import SubmitError, submit_application
from tests.distribution._publishing_fakes import (
    FakeCredentialStore, FakeIdentityHub, FakePublishHub, RecordingEvents,
    SPACE_COMMIT, write_application,
)
from tests.distribution.test_gitee_repository import NEW_SHA, PublishApi, tree


@pytest.mark.parametrize("side", [32, 700])
def test_submit_accepts_valid_icon_above_one_mib(tmp_path, side):
    write_application(tmp_path)
    output = BytesIO()
    pixels = random.Random(7).randbytes(side * side * 3)
    Image.frombytes("RGB", (side, side), pixels).save(output, format="PNG")
    (tmp_path / "icon.png").write_bytes(output.getvalue())
    assert check_application(tmp_path, watcherobot_version="0.1.1a3").icon == "icon.png"
    files = {path: (tmp_path / path).read_bytes() for path in ("app.json", "icon.png")}
    source_tree = tree(files)
    blobs = {item["sha"]: files[item["path"]] for item in source_tree["tree"]}

    class Api:
        def get_json(self, url, headers, *, timeout):
            if "/commits/" in url:
                return JsonResponse(200, {"sha": SPACE_COMMIT})
            path = url.split("/contents/")[1].split("?")[0]
            item = next(item for item in source_tree["tree"] if item["path"] == path)
            return JsonResponse(200, {**item, "type": "file", "encoding": "base64",
                "content": base64.b64encode(files[path]).decode()})

        def request(self, method, path, token, data=None):
            assert method == "GET" and token is None
            if "/git/trees/" in path:
                assert SPACE_COMMIT in path
                return 200, source_tree
            if "/git/blobs/" in path:
                return 200, {"encoding": "base64", "content": base64.b64encode(
                    blobs[path.rsplit("/", 1)[-1]]
                ).decode()}
            pytest.fail(f"Unexpected API request: {path}")

    api = Api()
    reader = GiteeRepository(api=api, public=GiteePublicRepository(transport=api))
    hub = FakePublishHub()
    hub.read_repository_file = reader.read_repository_file
    result = submit_application(
        tmp_path, provider="gitee", commit=SPACE_COMMIT,
        credentials=FakeCredentialStore(AccessToken("test")),
        identity_hub=FakeIdentityHub(), publish_hub=hub,
        events=RecordingEvents(), watcherobot_version="0.1.1a3",
    )
    assert result.pr_status == "pending"


@pytest.mark.parametrize("invalid", ["size", "digest", "symlink", "oversize"])
def test_single_file_read_retains_blob_safety_checks(invalid):
    payload = tree({"icon.png": b"abc"})
    item = payload["tree"][0]
    if invalid == "size":
        item["size"] = 2
    elif invalid == "digest":
        item["sha"] = "c" * 40
    elif invalid == "symlink":
        item["mode"] = "120000"
    else:
        item["size"] = MAX_SNAPSHOT_BYTES + 1

    class Api:
        def get_json(self, url, headers, *, timeout):
            if "/commits/" in url:
                return JsonResponse(200, {"sha": SPACE_COMMIT})
            return JsonResponse(200, {**item, "type": "symlink" if invalid == "symlink" else "file",
                "encoding": "base64", "content": "YWJj"})

        def request(self, method, path, token, data=None):
            if "/git/trees/" in path:
                return 200, payload
            assert invalid in {"size", "digest"}, "invalid metadata must fail before blob read"
            return 200, {"encoding": "base64", "content": "YWJj"}

    with pytest.raises(HubInvalidResponse):
        api = Api()
        GiteeRepository(api=api, public=GiteePublicRepository(transport=api)).read_repository_file(
            repo_id="alice/app", commit=SPACE_COMMIT, path="icon.png",
        )


class SubmissionApi:
    def __init__(self, *, file_commit=SPACE_COMMIT, failure=None):
        self.file_commit = file_commit
        self.failure = failure
        self.writes = []
        self.branch = None

    def request(self, method, path, token, data=None):
        if method == "GET" and "/commits?" in path:
            query = parse_qs(urlsplit(path).query)
            assert query["sha"] == [SPACE_COMMIT]
            assert query["path"] == ["app-list.json"]
            assert query["per_page"] == ["1"]
            return 200, [{"sha": self.file_commit}]
        if path == "repos/developer/catalog":
            return 200, {"parent": {"full_name": "team/catalog"}}
        if method == "POST" and path == "repos/developer/catalog/branches":
            self.writes.append((path, data))
            assert data["refs"] == SPACE_COMMIT
            self.branch = data["branch_name"]
            return 201, {"name": self.branch, "commit": {"sha": SPACE_COMMIT}}
        if method == "POST" and path.endswith("/commits"):
            self.writes.append((path, data))
            assert self.branch is not None, "Gitee requires an existing branch for commits"
            assert data["branch"] == self.branch
            assert "start_branch" not in data
            assert data["actions"][0]["last_commit_id"] == self.file_commit
            if self.failure == "timeout":
                raise HubNetworkError("connection timed out")
            if self.failure is not None:
                return self.failure, {}
            return 201, {"sha": NEW_SHA}
        if path == "repos/team/catalog":
            return 200, {"default_branch": "master"}
        if path.endswith("/pulls"):
            self.writes.append((path, data))
            return 201, {"number": 1, "title": "test", "html_url": "https://gitee.com/team/catalog/pulls/1"}
        raise AssertionError((method, path))


def submission_hub(api):
    hub = GiteeRepository(api=api, identity=FakeIdentityHub())
    hub.get_repository_head = lambda *a, **kw: SimpleNamespace(commit=SPACE_COMMIT)
    return hub


@pytest.mark.parametrize("failure", ["timeout", 429, 503])
def test_submission_preserves_network_error_and_does_not_retry(failure):
    api = SubmissionApi(failure=failure)
    with pytest.raises(HubNetworkError):
        submission_hub(api).create_catalog_pull_request(
            AccessToken("test"), repo_id="team/catalog", path="app-list.json",
            content=b"[]", parent_commit=SPACE_COMMIT, title="test", description="test",
        )
    assert len(api.writes) == 2
    assert api.writes[0][0].endswith("/branches")
    assert api.writes[1][0].endswith("/commits")


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("file_commit", [SPACE_COMMIT, "c" * 40])
def test_publish_uses_file_history_at_selected_revision(operation, file_commit):
    initial = {"app.json": b"old", "keep.txt": b"keep"}
    desired = {"keep.txt": b"keep"}
    if operation == "update":
        desired["app.json"] = b"new"

    class Api(PublishApi):
        def request(self, method, path, token, data=None):
            if method == "GET" and "/commits?" in path:
                query = parse_qs(urlsplit(path).query)
                assert query["sha"] == [SPACE_COMMIT]
                assert query["path"] == ["app.json"]
                assert query["per_page"] == ["1"]
                return 200, [{"sha": file_commit}]
            if method == "POST":
                action = next(item for item in data["actions"] if item["path"] == "app.json")
                assert action["action"] == operation
                assert action["last_commit_id"] == file_commit
            return super().request(method, path, token, data)

    GiteeRepository(api=Api(initial, desired)).replace_repository_files(
        AccessToken("test"), repo_id="alice/app",
        files=tuple(UploadFile.from_bytes(path, content) for path, content in desired.items()),
        commit_message="test",
    )


@pytest.mark.parametrize("file_commit", [SPACE_COMMIT, "c" * 40])
def test_submission_uses_catalog_file_commit_separately_from_branch_start(file_commit):
    result = submission_hub(SubmissionApi(file_commit=file_commit)).create_catalog_pull_request(
        AccessToken("test"), repo_id="team/catalog", path="app-list.json",
        content=b"[]", parent_commit=SPACE_COMMIT, title="test", description="test",
    )
    assert result.number == 1


@pytest.mark.parametrize("branch_response", [
    {}, {"commit": {"sha": "c" * 40}}, {"commit": None},
])
def test_submission_verifies_branch_start_before_file_write(branch_response):
    class Api(SubmissionApi):
        def request(self, method, path, token, data=None):
            if method == "POST":
                assert path.endswith("/branches"), "must not commit on an unverified branch"
                return 201, branch_response
            return super().request(method, path, token, data)

    with pytest.raises(HubInvalidResponse):
        submission_hub(Api()).create_catalog_pull_request(
            AccessToken("test"), repo_id="team/catalog", path="app-list.json",
            content=b"[]", parent_commit=SPACE_COMMIT, title="test", description="test",
        )


@pytest.mark.parametrize("payload", [[], {}, [{"sha": "not-a-commit"}], [None]])
def test_invalid_file_history_prevents_remote_mutation(payload):
    class Api(PublishApi):
        def request(self, method, path, token, data=None):
            assert method == "GET", "must not publish with an invalid file guard"
            if "/commits?" in path:
                return 200, payload
            return super().request(method, path, token, data)

    with pytest.raises(HubInvalidResponse):
        GiteeRepository(api=Api({"app.json": b"old"})).replace_repository_files(
            AccessToken("test"), repo_id="alice/app",
            files=(UploadFile.from_bytes("app.json", b"new"),), commit_message="test",
        )


@pytest.mark.parametrize("failure, hint", [
    (HubNetworkError("private-server-response"), "may have succeeded"),
    (HubRateLimitError("private-server-response"), "rate limit"),
])
def test_submit_reports_safe_network_recovery_hint(tmp_path, failure, hint):
    write_application(tmp_path)
    hub = FakePublishHub()

    def fail(**kwargs):
        raise failure

    hub.create_catalog_pull_request = lambda *args, **kwargs: fail(**kwargs)
    with pytest.raises(SubmitError) as caught:
        submit_application(
            tmp_path, provider="gitee", commit=SPACE_COMMIT,
            credentials=FakeCredentialStore(AccessToken("test")),
            identity_hub=FakeIdentityHub(), publish_hub=hub,
            events=RecordingEvents(), watcherobot_version="0.1.1a3",
        )
    assert caught.value.code == ErrorCode.REMOTE_ERROR
    assert hint in str(caught.value)
    assert "private-server-response" not in str(caught.value)
