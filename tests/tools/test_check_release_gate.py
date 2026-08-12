from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def _load_module():
    path = ROOT / "tools" / "check_release_gate.py"
    spec = importlib.util.spec_from_file_location("check_release_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exactly_one_release_request_label_is_required() -> None:
    module = _load_module()

    assert module.select_release_label(["docs", "release:minor"]) == "minor"
    with pytest.raises(ValueError, match="exactly one"):
        module.select_release_label(["docs"])
    with pytest.raises(ValueError, match="exactly one"):
        module.select_release_label(["release:minor", "release:major"])


def test_release_version_pr_contract_is_strict() -> None:
    module = _load_module()

    module.validate_version_pull_request(
        merged=True,
        base_ref="main",
        labels=["release:version"],
        head_ref="release/watcherobot-0.1.1a3",
        version="0.1.1a3",
        merge_commit_sha="abc123",
        tag_sha="abc123",
    )
    with pytest.raises(ValueError, match="merged into main"):
        module.validate_version_pull_request(
            merged=False,
            base_ref="main",
            labels=["release:version"],
            head_ref="release/watcherobot-0.1.1a3",
            version="0.1.1a3",
            merge_commit_sha="abc123",
            tag_sha="abc123",
        )
    with pytest.raises(ValueError, match="release:version"):
        module.validate_version_pull_request(
            merged=True,
            base_ref="main",
            labels=[],
            head_ref="release/watcherobot-0.1.1a3",
            version="0.1.1a3",
            merge_commit_sha="abc123",
            tag_sha="abc123",
        )


def test_release_tag_must_target_version_pr_merge_commit() -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="merge commit"):
        module.validate_version_pull_request(
            merged=True,
            base_ref="main",
            labels=["release:version"],
            head_ref="release/watcherobot-0.1.1a4",
            version="0.1.1a4",
            merge_commit_sha="merge123",
            tag_sha="bump123",
        )


@pytest.mark.parametrize(
    "status_code",
    [200, 301, 302],
)
def test_existing_index_version_is_rejected(status_code: int) -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="already exists"):
        module.validate_version_absent("PyPI", "0.1.1a3", status_code)


def test_missing_index_version_is_accepted() -> None:
    module = _load_module()

    module.validate_version_absent("PyPI", "0.1.1a3", 404)


def test_existing_test_index_version_requires_a_matching_draft() -> None:
    module = _load_module()

    module.validate_version_absent("TestPyPI", "0.1.1a3", 200, allow_existing=True)


def test_matching_draft_release_can_be_reused() -> None:
    module = _load_module()

    assert module.validate_existing_release(
        tag="v0.1.1a3",
        sha="abc123",
        release={"tagName": "v0.1.1a3", "targetCommitish": "abc123", "isDraft": True},
    ) is True


@pytest.mark.parametrize(
    "release",
    [
        {"tagName": "v0.1.1a3", "targetCommitish": "different", "isDraft": True},
        {"tagName": "v0.1.1a3", "targetCommitish": "abc123", "isDraft": False},
    ],
)
def test_nonmatching_or_published_release_is_rejected(release: dict[str, object]) -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="conflicting GitHub Release"):
        module.validate_existing_release(tag="v0.1.1a3", sha="abc123", release=release)
