import pytest
from watcherobot.distribution.download import download_application_snapshot
from watcherobot.distribution.install import install_application


def test_unknown_provider_fails_before_local_side_effects(tmp_path):
    with pytest.raises(ValueError):
        install_application(
            provider="unknown",
            repo_id="user/app",
            commit="a" * 40,
            store_root=tmp_path / "store",
            runtime_root=tmp_path / "runtime",
            hub=None,
        )
    with pytest.raises(ValueError):
        download_application_snapshot(
            provider="unknown",
            repo_id="user/app",
            commit="a" * 40,
            target=tmp_path,
            hub=None,
        )
    assert not list(tmp_path.iterdir())


from watcherobot.distribution.cli import build_parser
from watcherobot.distribution.catalog_submission import (
    parse_catalog_entries,
    CatalogDocumentError,
)


def test_catalog_accepts_only_generic_repository_identifier():
    entry = parse_catalog_entries(
        b'[{"repo_id":"user/app","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]'
    )[0]
    assert entry.repo_id == "user/app"
    with pytest.raises(CatalogDocumentError):
        parse_catalog_entries(
            b'[{"space_id":"user/app","commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]'
        )


@pytest.mark.parametrize(
    "command,extra",
    [
        ("publish", ["."]),
        ("submit", ["."]),
        ("marketplace", []),
        ("download", ["--repo-id", "user/app", "--commit", "a" * 40, "--target", "."]),
        (
            "install",
            [
                "--repo-id",
                "user/app",
                "--commit",
                "a" * 40,
                "--store-root",
                ".",
                "--runtime-root",
                ".",
            ],
        ),
    ],
)
def test_remote_commands_require_provider(command, extra):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["app", command, *extra])
    assert (
        build_parser()
        .parse_args(["app", command, *extra, "--provider", "gitee"])
        .provider
        == "gitee"
    )
