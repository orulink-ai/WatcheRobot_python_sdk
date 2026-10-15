"""Git acquisition contracts use local objects, never a system Git executable."""

import json
import pytest
from dulwich.repo import Repo
from dulwich.objects import Blob, Tree, Commit
from watcherobot.distribution.gitee_git import GiteeGitRepository
from watcherobot.distribution.ports import HubInvalidResponse, HubRevisionNotFound

REPO = "alice/WatcherRobot-com.example.demo"


def revision(repo, files, parent=None):
    tree = Tree()
    for name, content, mode in files:
        blob = Blob.from_string(content)
        repo.object_store.add_object(blob)
        tree.add(name.encode(), mode, blob.id)
    repo.object_store.add_object(tree)
    commit = Commit()
    commit.tree = tree.id
    commit.parents = [parent] if parent else []
    commit.author = commit.committer = b"Tester <test@example.com>"
    commit.author_time = commit.commit_time = 1
    commit.author_timezone = commit.commit_timezone = 0
    commit.message = b"test"
    repo.object_store.add_object(commit)
    return commit.id.decode()


class Transport:
    def __init__(self, remote, head):
        self.remote, self.head, self.calls = remote, head, []

    def latest(self, repo_id):
        return self.head

    def fetch(self, repo_id, target, commit, depth):
        self.calls.append((repo_id, commit, depth))
        for oid in self.remote.object_store:
            target.object_store.add_object(self.remote[oid])


@pytest.fixture
def setup(tmp_path):
    remote = Repo.init_bare(str(tmp_path / "remote"), mkdir=True)
    old = revision(remote, [("app.json", b'{"version":"0.2.1"}', 0o100644)])
    new = revision(
        remote, [("app.json", b'{"version":"9.9.9"}', 0o100644)], old.encode()
    )
    transport = Transport(remote, new)
    reader = GiteeGitRepository(cache_root=tmp_path / "cache", transport=transport)
    yield reader, transport, old, new, remote
    remote.close()


def test_reads_reviewed_commit_not_head_and_reuses_disk_cache(setup, tmp_path):
    reader, transport, old, _, _ = setup
    assert (
        json.loads(
            reader.read_repository_file(repo_id=REPO, commit=old, path="app.json")
        )["version"]
        == "0.2.1"
    )
    other = GiteeGitRepository(cache_root=tmp_path / "cache", transport=transport)
    target = tmp_path / "out"
    target.mkdir()
    progress = []
    result = other.download_repository_snapshot_with_progress(
        repo_id=REPO,
        commit=old,
        target=target,
        on_progress=lambda *x: progress.append(x),
    )
    assert result.commit == old
    assert json.loads((target / "app.json").read_text())["version"] == "0.2.1"
    assert len(transport.calls) == 1
    assert progress[-1][0] == progress[-1][1]
    assert not (target / ".git").exists()


def test_catalog_always_checks_master(setup):
    reader, transport, old, new, remote = setup
    transport.head = revision(remote, [("app-list.json", b"[]", 0o100644)])
    one = reader.read_public_catalog(
        repo_id="orulink-sz/watcherobot-app-store", path="app-list.json"
    )
    transport.head = revision(remote, [("app-list.json", b"[1]", 0o100644)])
    two = reader.read_public_catalog(
        repo_id="orulink-sz/watcherobot-app-store", path="app-list.json"
    )
    assert one.commit != two.commit
    assert two.content == b"[1]"


@pytest.mark.parametrize(
    "name,mode,content",
    [
        ("CON", 0o100644, b"x"),
        (".git", 0o100644, b"x"),
        ("link", 0o120000, b"../../outside"),
        (
            "file",
            0o100644,
            b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 10\n",
        ),
    ],
)
def test_rejects_unsafe_or_incomplete_tree(setup, name, mode, content):
    reader, transport, _, _, remote = setup
    sha = revision(remote, [("app.json", b"{}", 0o100644), (name, content, mode)])
    with pytest.raises(HubInvalidResponse):
        reader.read_repository_file(repo_id=REPO, commit=sha, path="app.json")
    assert not any((reader.cache_root).glob("*/complete.json"))


def test_rejects_tree_as_commit_and_never_uses_latest(setup):
    reader, transport, old, new, remote = setup
    with pytest.raises(HubInvalidResponse):
        reader.read_repository_file(
            repo_id=REPO, commit=remote[old.encode()].tree.decode(), path="app.json"
        )
    with pytest.raises(HubRevisionNotFound):
        reader.read_repository_file(repo_id=REPO, commit="a" * 40, path="app.json")
    assert len(transport.calls) <= 4


def test_invalid_reference_makes_no_request(setup):
    reader, transport, old, _, _ = setup
    with pytest.raises(HubInvalidResponse):
        reader.read_repository_file(repo_id="evil/../repo", commit=old, path="app.json")
    assert not transport.calls


def test_export_failure_keeps_target_empty(setup, tmp_path):
    reader, transport, old, _, _ = setup
    target = tmp_path / "out"
    target.mkdir()
    (target / "existing").write_text("keep")
    with pytest.raises(HubInvalidResponse):
        reader.download_repository_snapshot(repo_id=REPO, commit=old, target=target)
    assert (target / "existing").read_text() == "keep"


def test_cli_routes_browser_and_download_to_git():
    from watcherobot.distribution.cli import (
        _build_marketplace_dependencies,
        _build_download_dependencies,
        _build_install_dependencies,
    )

    assert isinstance(_build_marketplace_dependencies("gitee").hub, GiteeGitRepository)
    assert isinstance(_build_download_dependencies("gitee").hub, GiteeGitRepository)
    assert isinstance(_build_install_dependencies("gitee").hub, GiteeGitRepository)


def test_refused_sha_falls_back_to_bounded_history(setup, tmp_path):

    reader, transport, old, _, remote = setup

    class History(Transport):
        def fetch(self, repo_id, target, commit, depth):
            if depth == 1:
                self.calls.append((repo_id, commit, depth))
                raise HubRevisionNotFound("not our ref")
            return super().fetch(repo_id, target, commit, depth)

    history = History(remote, transport.head)
    reader.transport = history
    assert b"0.2.1" in reader.read_repository_file(
        repo_id=REPO, commit=old, path="app.json"
    )
    assert [x[2] for x in history.calls] == [1, 32]


def test_network_failure_is_not_retried_and_staging_is_removed(setup):
    from watcherobot.distribution.ports import HubNetworkError

    reader, transport, old, _, _ = setup

    def fail(*args):
        raise HubNetworkError("offline")

    transport.fetch = fail
    with pytest.raises(HubNetworkError):
        reader.read_repository_file(repo_id=REPO, commit=old, path="app.json")
    assert not list(reader.cache_root.glob(".fetch-*"))
    assert not list(reader.cache_root.glob("*/complete.json"))


def test_cache_prunes_old_entries_but_keeps_current(setup, monkeypatch):
    import watcherobot.distribution.gitee_git as module

    reader, transport, old, new, _ = setup
    reader.read_repository_file(repo_id=REPO, commit=old, path="app.json")
    monkeypatch.setattr(module, "_MAX_CACHE_BYTES", 1)
    reader.read_repository_file(repo_id=REPO, commit=new, path="app.json")
    assert len(list(reader.cache_root.glob("*/complete.json"))) == 1
    assert not list(reader.cache_root.glob("*-" + old))


def test_rejects_oversized_snapshot_before_export(setup, monkeypatch):
    import watcherobot.distribution.gitee_git as module

    reader, transport, old, _, _ = setup
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 1)
    with pytest.raises(HubInvalidResponse):
        reader.read_repository_file(repo_id=REPO, commit=old, path="app.json")
    assert not list(reader.cache_root.glob("*/complete.json"))


def test_revalidates_corrupt_disk_cache(setup):
    reader, transport, old, _, _ = setup
    reader.read_repository_file(repo_id=REPO, commit=old, path="app.json")
    marker = next(reader.cache_root.glob("*/complete.json"))
    marker.write_text("{}")
    assert b"0.2.1" in reader.read_repository_file(
        repo_id=REPO, commit=old, path="app.json"
    )
    assert len(transport.calls) == 2


def test_git_transport_blocks_redirects_and_bounds_network_reads(monkeypatch):
    import watcherobot.distribution.gitee_git as module
    from dulwich.client import HttpGitClient
    from watcherobot.distribution.ports import HubNetworkError

    class Response:
        def close(self):
            pass

    monkeypatch.setattr(
        HttpGitClient, "_http_request", lambda *a: (Response(), lambda n: b"x" * n)
    )
    monkeypatch.setattr(module, "_MAX_TRANSFER_BYTES", 4)
    client = module._BoundedClient(
        REPO, deadline=module.time.monotonic() + 10, budget=[0]
    )
    with pytest.raises(HubNetworkError):
        client._http_request("https://evil.example/repo")
    _, read = client._http_request("https://gitee.com/" + REPO + ".git/git-upload-pack")
    with pytest.raises(HubInvalidResponse):
        read(5)
    client.pool_manager.clear()


def test_cancelled_export_leaves_no_partial_files(setup, tmp_path):
    reader, _, old, _, _ = setup
    target = tmp_path / "cancelled"
    target.mkdir()

    def cancel(*args):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        reader.download_repository_snapshot_with_progress(
            repo_id=REPO, commit=old, target=target, on_progress=cancel
        )
    assert not list(target.iterdir())


def test_git_cache_merges_concurrent_same_commit_fetches(setup):
    from concurrent.futures import ThreadPoolExecutor

    reader, transport, old, _, _ = setup

    def read(_):
        return reader.read_repository_file(repo_id=REPO, commit=old, path="app.json")

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(read, range(2)))
    assert values[0] == values[1]
    assert len(transport.calls) == 1
