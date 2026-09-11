from pathlib import Path
import hashlib
from types import SimpleNamespace

import pytest

from watcherobot.distribution.gitee_repository import GiteeRepository
from watcherobot.distribution.ports import (
    AccessToken,
    HubInvalidResponse,
    HubRepositoryConflict,
    UploadFile,
)

SHA = "a" * 40


@pytest.mark.parametrize("mode", ["40000", "040000"])
def test_gitee_directory_modes_are_supported(tmp_path, mode):
    api = Api({"tree": [
        {"path": "web", "type": "tree", "mode": mode},
        {"path": "web/app.js", "type": "blob", "mode": "100644",
         "size": 4, "sha": hashlib.sha1(b"blob 4\0pass").hexdigest()},
    ]})
    public = SimpleNamespace(read_file=lambda **kwargs: b"pass")
    GiteeRepository(api=api, public=public).download_repository_snapshot(
        repo_id="alice/app", commit=SHA, target=tmp_path)
    assert (tmp_path / "web/app.js").read_bytes() == b"pass"


def test_submission_pushes_only_developer_fork():
    class ForkApi(Api):
        def request(self, method, path, token, data=None):
            self.calls.append((method, path, token, data))
            if method == "POST":
                return 201, {
                    "number": 3,
                    "title": "review",
                    "html_url": "https://gitee.com/team/catalog/pulls/3",
                }
            return 200, {
                "parent": {"full_name": "team/catalog"},
                "default_branch": "master",
            }

    api = ForkApi(None)
    git = Git()
    identity = SimpleNamespace(whoami=lambda token: SimpleNamespace(username="alice"))
    hub = GiteeRepository(api=api, git=git, identity=identity)
    hub.get_repository_head = lambda *a, **kw: SimpleNamespace(commit=SHA)
    result = hub.create_catalog_pull_request(
        AccessToken("test"),
        repo_id="team/catalog",
        path="app-list.json",
        content=b"[]",
        parent_commit=SHA,
        title="review",
        description="manual",
    )
    assert result.number == 3
    assert [args for args, _ in git.calls if args[0] == "clone"][0][
        -2
    ] == "https://gitee.com/alice/catalog.git"
    pushes = [args for args, _ in git.calls if args[0] == "push"]
    assert len(pushes) == 1 and pushes[0][1] == "origin"
    assert pushes[0][2].startswith("watcher-submit-")
    writes = [call for call in api.calls if call[0] != "GET"]
    assert len(writes) == 1 and writes[0][1] == "repos/team/catalog/pulls"
    assert writes[0][3]["head"].startswith("alice/catalog:watcher-submit-")


class Api:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, path, token, data=None):
        self.calls.append((method, path, token, data))
        return 200, self.payload


class Git:
    def __init__(self):
        self.calls = []

    def run(self, root, *args, token=None):
        self.calls.append((args, token))
        if args[0] == "clone":
            (root / "repo").mkdir()
        return ""


def test_publish_atomic_non_force_push():
    git = Git()
    hub = GiteeRepository(git=git)
    token = AccessToken("unit-test-secret")
    hub.replace_repository_files(
        token,
        repo_id="alice/WatcherRobot-demo",
        files=(UploadFile.from_bytes("app.py", b"pass"),),
        commit_message="publish",
    )
    assert [args for args, _ in git.calls if args[0] == "push"] == [
        ("push", "origin", "HEAD")
    ]
    assert all(token.value not in repr(args) for args, _ in git.calls)
    assert ("read-tree", "--empty") in [args for args, _ in git.calls]


def test_publish_rejects_git_metadata_before_remote_write():
    git = Git()
    with pytest.raises(HubInvalidResponse):
        GiteeRepository(git=git).replace_repository_files(
            AccessToken("test"),
            repo_id="alice/app",
            files=(UploadFile.from_bytes(".git/config", b"unsafe"),),
            commit_message="publish",
        )
    assert not git.calls


def test_private_existing_repository_is_not_made_public():
    api = Api({"private": True})
    identity = SimpleNamespace(whoami=lambda token: SimpleNamespace(username="alice"))
    with pytest.raises(HubRepositoryConflict):
        GiteeRepository(api=api, identity=identity).ensure_public_repository(
            AccessToken("test"), repo_id="alice/app", sdk="static"
        )
    assert [call[0] for call in api.calls] == ["GET"]


@pytest.mark.parametrize(
    "path,mode",
    [
        ("../escape", "100644"),
        (".git/config", "100644"),
        ("link", "120000"),
        ("module", "160000"),
        ("trailing.", "100644"),
    ],
)
def test_download_rejects_unsafe_tree_before_writes(tmp_path, path, mode):
    api = Api({"tree": [{"path": path, "type": "blob", "mode": mode, "size": 1}]})
    with pytest.raises(HubInvalidResponse):
        GiteeRepository(api=api).download_repository_snapshot(
            repo_id="alice/app", commit=SHA, target=tmp_path
        )
    assert not list(tmp_path.iterdir())
    assert all(call[2] is None for call in api.calls)


def test_download_exact_sha_and_cleanup_on_failure(tmp_path):
    api = Api(
        {
            "tree": [
                {
                    "path": "app.py",
                    "type": "blob",
                    "mode": "100644",
                    "size": 4,
                    "sha": hashlib.sha1(b"blob 4\0pass").hexdigest(),
                }
            ]
        }
    )
    public = SimpleNamespace(read_file=lambda **kwargs: b"pass")
    revision = GiteeRepository(api=api, public=public).download_repository_snapshot(
        repo_id="alice/app", commit=SHA, target=tmp_path
    )
    assert revision.commit == SHA
    assert (tmp_path / "app.py").read_bytes() == b"pass"
    assert SHA in api.calls[0][1]


def test_wrong_fork_never_receives_writes():
    api = Api({"parent": {"full_name": "other/catalog"}})
    git = Git()
    identity = SimpleNamespace(whoami=lambda token: SimpleNamespace(username="alice"))
    hub = GiteeRepository(api=api, git=git, identity=identity)
    hub.get_repository_head = lambda *a, **kw: SimpleNamespace(commit=SHA)
    with pytest.raises(HubRepositoryConflict):
        hub.create_catalog_pull_request(
            AccessToken("test"),
            repo_id="team/catalog",
            path="app-list.json",
            content=b"[]",
            parent_commit=SHA,
            title="review",
            description="manual",
        )
    assert not git.calls
    assert all(call[0] == "GET" for call in api.calls)


def test_publish_rejects_undownloadable_file_before_git():
    git = Git()
    with pytest.raises(HubInvalidResponse):
        GiteeRepository(git=git).replace_repository_files(
            AccessToken("test"),
            repo_id="alice/app",
            files=(UploadFile.from_bytes("asset.bin", b"x" * (1024 * 1024 + 1)),),
            commit_message="publish",
        )
    assert not git.calls


def test_download_tree_hash_mismatch_rolls_back(tmp_path):
    api = Api(
        {
            "tree": [
                {
                    "path": path,
                    "type": "blob",
                    "mode": "100644",
                    "size": 4,
                    "sha": digest,
                }
                for path, digest in [
                    ("first.py", hashlib.sha1(b"blob 4\0pass").hexdigest()),
                    ("bad.py", SHA),
                ]
            ]
        }
    )
    public = SimpleNamespace(read_file=lambda **kwargs: b"pass")
    with pytest.raises(HubInvalidResponse, match="immutable tree"):
        GiteeRepository(api=api, public=public).download_repository_snapshot(
            repo_id="alice/app", commit=SHA, target=tmp_path
        )
    assert not list(tmp_path.iterdir())
