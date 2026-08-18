from __future__ import annotations

import importlib.util
import json
import subprocess
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


def test_recovery_gate_can_read_version_from_checked_out_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    version_file = tmp_path / "__init__.py"
    version_file.write_text('__version__ = "0.1.1"\n', encoding="utf-8")

    def fake_run(*args: str) -> str:
        if args[:3] == ("git", "cat-file", "-t"):
            return "tag"
        if args[:3] == ("git", "rev-list", "-n"):
            return "abc123"
        if args[:2] in (("git", "fetch"), ("git", "merge-base")):
            return ""
        raise AssertionError(f"unexpected command: {args!r}")

    monkeypatch.setattr(module, "_run", fake_run)
    monkeypatch.setattr(
        module,
        "_associated_pull_requests",
        lambda _repository, _sha: [
            {
                "merged_at": "2026-08-18T00:00:00Z",
                "base": {"ref": "main"},
                "head": {"ref": "release/watcherobot-0.1.1"},
                "labels": [{"name": "release:version"}],
                "merge_commit_sha": "abc123",
            }
        ],
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "tagName": "v0.1.1",
                    "targetCommitish": "abc123",
                    "isDraft": True,
                }
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "_http_status",
        lambda _url: 200,
    )

    result = module.validate_gate(
        repository="orulink-ai/WatcheRobot_python_sdk",
        tag="v0.1.1",
        sha="abc123",
        version_file=version_file,
        allow_existing_pypi=True,
    )

    assert result.version == "0.1.1"
    assert result.reuse_artifact is True
