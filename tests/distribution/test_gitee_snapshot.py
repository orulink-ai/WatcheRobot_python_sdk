import base64
import hashlib

import pytest

from watcherobot.distribution.gitee_repository import GiteeRepository
from watcherobot.distribution.ports import HubInvalidResponse

SHA = "a" * 40


class Api:
    def __init__(self, tree, blobs):
        self.tree = tree
        self.blobs = blobs
        self.calls = []

    def request(self, method, path, token, data=None):
        self.calls.append((method, path, token, data))
        if "/commits/" in path:
            return 200, {"sha": SHA}
        if "/git/trees/" in path:
            return 200, self.tree
        if "/git/blobs/" in path:
            sha = path.rsplit("/", 1)[-1]
            content = self.blobs[sha]
            return 200, {
                "encoding": "base64",
                "content": base64.b64encode(content).decode(),
            }
        raise AssertionError(path)


def entry(path, content, mode="100644"):
    digest = hashlib.sha1(
        f"blob {len(content)}\0".encode() + content,
        usedforsecurity=False,
    ).hexdigest()
    return {"path": path, "type": "blob", "mode": mode, "size": len(content), "sha": digest}


def test_snapshot_uses_anonymous_gitee_api_without_system_git(tmp_path):
    files = {"app.json": b"{}\n", "web/asset.bin": bytes(range(256))}
    items = [entry(path, content) for path, content in files.items()]
    api = Api({"tree": items, "truncated": False}, {item["sha"]: files[item["path"]] for item in items})
    target = tmp_path / "result"
    target.mkdir()

    revision = GiteeRepository(api=api).download_repository_snapshot(
        repo_id="owner/app", commit=SHA, target=target,
    )

    assert revision.commit == SHA
    assert (target / "app.json").read_bytes() == files["app.json"]
    assert (target / "web/asset.bin").read_bytes() == files["web/asset.bin"]
    assert all(call[2] is None for call in api.calls)


def test_snapshot_rejects_symlinks_before_blob_reads(tmp_path):
    api = Api(
        {"tree": [{"path": "link", "type": "blob", "mode": "120000", "size": 1, "sha": SHA}]},
        {},
    )
    with pytest.raises(HubInvalidResponse):
        GiteeRepository(api=api).download_repository_snapshot(
            repo_id="owner/app", commit=SHA, target=tmp_path,
        )
    assert len(api.calls) == 2
    assert not any("/git/blobs/" in call[1] for call in api.calls)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name,size", [("CON.txt", 1), ("huge.bin", 100 * 1024 * 1024 + 1)])
def test_export_limits_apply_before_read(tmp_path, name, size):
    tree = {"tree": [{"path": name, "size": size, "type": "blob", "mode": "100644", "sha": SHA}]}
    with pytest.raises(HubInvalidResponse):
        GiteeRepository()._export_snapshot(
            "owner/app", SHA, tmp_path, tree,
            lambda **kwargs: pytest.fail("Must not read"),
        )
    assert not list(tmp_path.iterdir())


def test_nonempty_target_is_preserved_without_network(tmp_path):
    (tmp_path / "keep").write_bytes(b"keep")
    with pytest.raises(HubInvalidResponse):
        GiteeRepository().download_repository_snapshot(
            repo_id="owner/app", commit=SHA, target=tmp_path,
        )
    assert (tmp_path / "keep").read_bytes() == b"keep"


def test_large_unicode_blob_is_downloaded_by_blob_sha(tmp_path):
    path = "资源/示例音频 (1).bin"
    data = bytes(range(256)) * 8192
    item = entry(path, data)
    api = Api({"tree": [item], "truncated": False}, {item["sha"]: data})

    GiteeRepository(api=api).download_repository_snapshot(
        repo_id="owner/app", commit=SHA, target=tmp_path,
    )

    assert (tmp_path / path).read_bytes() == data
