"""Publication and export must enforce the same portable path contract."""

import hashlib

import pytest

from watcherobot.distribution.cli import build_parser
from watcherobot.distribution.gitee_repository import GiteeRepository
from watcherobot.distribution.ports import AccessToken, HubInvalidResponse, UploadFile


@pytest.mark.parametrize(
    "paths",
    [
        ("CON.txt",),
        ("assets/AUX/icon.png",),
        ("lpt9.log",),
        ("assets./icon.png",),
        ("assets /icon.png",),
        ("icon.png ",),
        ("Icon.png", "icon.png"),
        ("icon.png", "icon.png"),
        (".GIT/config",),
        ("Assets/a.txt", "assets/b.txt"),
        ("assets/Sub/a.txt", "assets/sub/b.txt"),
        ("assets", "assets/a.txt"),
        ("assets/a.txt", "assets"),
    ],
)
@pytest.mark.parametrize("operation", ["publish", "export"])
def test_unsafe_paths_rejected_before_io(tmp_path, paths, operation):
    class NoGit:
        def run(self, *args, **kwargs):
            pytest.fail("Invalid paths must be rejected before Git operations")

    repository = GiteeRepository(git=NoGit())
    with pytest.raises(HubInvalidResponse):
        if operation == "publish":
            repository.replace_repository_files(
                AccessToken("test"),
                repo_id="alice/app",
                files=tuple(UploadFile.from_bytes(path, b"x") for path in paths),
                commit_message="test",
            )
        else:
            tree = {"tree": [
                dict(path=path, size=1, type="blob", mode="100644", sha="a" * 40)
                for path in paths
            ]}
            repository._export_snapshot(
                "alice/app", "a" * 40, tmp_path, tree,
                lambda **kwargs: pytest.fail("Invalid paths must be rejected before reads"),
            )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["publish", "submit", "download", "install"])
def test_repository_help_is_provider_neutral(command, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["app", command, "--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "Space" not in help_text
    assert "repository" in help_text.lower()


@pytest.mark.parametrize("directories_first", [True, False])
def test_shared_directories_publish_and_export(tmp_path, directories_first):
    paths = ("assets/a.txt", "assets/sub/b.txt", "assets/sub/c.txt")

    class LocalGit:
        def run(self, root, *args, **kwargs):
            from watcherobot.distribution.gitee_repository import GiteeGit
            if args[0] == "clone":
                (root / "repo").mkdir()
                GiteeGit().run(root / "repo", "init", "--template=")
            if args[0] in {"hash-object", "update-index", "ls-files", "read-tree"}:
                return GiteeGit().run(root, *args)
            return ""

    GiteeRepository(git=LocalGit()).replace_repository_files(
        AccessToken("test"), repo_id="alice/app",
        files=tuple(UploadFile.from_bytes(path, b"x") for path in paths),
        commit_message="test",
    )
    digest = hashlib.sha1(b"blob 1\0x", usedforsecurity=False).hexdigest()
    files = [dict(path=path, size=1, type="blob", mode="100644", sha=digest)
             for path in paths]
    directories = [dict(path=path, type="tree", mode="040000")
                   for path in ("assets", "assets/sub")]
    tree = {"tree": directories + files if directories_first else files + directories}
    GiteeRepository()._export_snapshot(
        "alice/app", "a" * 40, tmp_path, tree, lambda **kwargs: b"x",
    )
    assert all((tmp_path / path).read_bytes() == b"x" for path in paths)
