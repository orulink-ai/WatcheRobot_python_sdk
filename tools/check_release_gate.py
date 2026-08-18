"""Fail closed unless a release tag satisfies the repository release contract."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from check_release_version import read_package_version, validate_release_tag


_REQUEST_LABELS = {
    "release:prerelease": "prerelease",
    "release:stable": "stable",
    "release:minor": "minor",
    "release:major": "major",
}


@dataclass(frozen=True)
class ReleaseGateResult:
    version: str
    reuse_artifact: bool


def select_release_label(labels: Iterable[str]) -> str:
    matches = [_REQUEST_LABELS[label] for label in labels if label in _REQUEST_LABELS]
    if len(matches) != 1:
        raise ValueError("exactly one release request label is required")
    return matches[0]


def validate_version_pull_request(
    *,
    merged: bool,
    base_ref: str,
    labels: Iterable[str],
    head_ref: str,
    version: str,
    merge_commit_sha: str,
    tag_sha: str,
) -> None:
    if not merged or base_ref != "main":
        raise ValueError("release version PR must be merged into main")
    if "release:version" not in labels:
        raise ValueError("release version PR must have the release:version label")
    expected_head = f"release/watcherobot-{version}"
    if head_ref != expected_head:
        raise ValueError(f"release version PR head must be {expected_head!r}")
    if merge_commit_sha != tag_sha:
        raise ValueError("release tag must target the version PR merge commit")


def validate_version_absent(
    index_name: str,
    version: str,
    status_code: int,
    *,
    allow_existing: bool = False,
) -> None:
    if status_code != 404 and not allow_existing:
        raise ValueError(f"watcherobot {version} already exists on {index_name}")


def validate_existing_release(
    *,
    tag: str,
    sha: str,
    release: dict[str, object],
) -> bool:
    if (
        release.get("tagName") == tag
        and release.get("targetCommitish") == sha
        and release.get("isDraft") is True
    ):
        return True
    raise ValueError(f"conflicting GitHub Release already exists for {tag}")


def _run(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def _http_status(url: str) -> int:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "watcherobot-release-gate/1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _associated_pull_requests(repository: str, sha: str) -> list[dict[str, object]]:
    output = _run(
        "gh",
        "api",
        f"repos/{repository}/commits/{sha}/pulls",
        "-H",
        "Accept: application/vnd.github+json",
    )
    value = json.loads(output)
    if not isinstance(value, list):
        raise ValueError("GitHub returned an invalid pull request list")
    return value


def validate_gate(
    *,
    repository: str,
    tag: str,
    sha: str,
    version_file: Path | None = None,
    allow_existing_pypi: bool = False,
    defer_draft_validation: bool = False,
) -> ReleaseGateResult:
    version = validate_release_tag(
        tag,
        read_package_version(version_file) if version_file is not None else read_package_version(),
    )
    tag_type = _run("git", "cat-file", "-t", tag)
    if tag_type != "tag":
        raise ValueError("release ref must be an annotated tag")
    resolved_sha = _run("git", "rev-list", "-n", "1", tag)
    if resolved_sha != sha:
        raise ValueError("resolved annotated tag commit does not match the release gate input")
    _run("git", "fetch", "origin", "main:refs/remotes/origin/main", "--no-tags")
    _run("git", "merge-base", "--is-ancestor", sha, "refs/remotes/origin/main")

    pull_requests = _associated_pull_requests(repository, sha)
    matching = []
    for pull_request in pull_requests:
        labels = [label.get("name") for label in pull_request.get("labels", []) if isinstance(label, dict)]
        try:
            validate_version_pull_request(
                merged=pull_request.get("merged_at") is not None,
                base_ref=str((pull_request.get("base") or {}).get("ref") or ""),
                labels=[str(label) for label in labels if label],
                head_ref=str((pull_request.get("head") or {}).get("ref") or ""),
                version=version,
                merge_commit_sha=str(pull_request.get("merge_commit_sha") or ""),
                tag_sha=sha,
            )
        except ValueError:
            continue
        matching.append(pull_request)
    if len(matching) != 1:
        raise ValueError("tag commit must be associated with exactly one valid release version PR")

    reusable_draft = False
    if not defer_draft_validation:
        release_result = subprocess.run(
            (
                "gh",
                "release",
                "view",
                tag,
                "--repo",
                repository,
                "--json",
                "tagName,targetCommitish,isDraft",
            ),
            check=False,
            text=True,
            capture_output=True,
        )
        if release_result.returncode == 0:
            release = json.loads(release_result.stdout)
            if not isinstance(release, dict):
                raise ValueError("GitHub returned invalid Release metadata")
            reusable_draft = validate_existing_release(tag=tag, sha=sha, release=release)

    validate_version_absent(
        "PyPI",
        version,
        _http_status(f"https://pypi.org/pypi/watcherobot/{version}/json"),
        allow_existing=allow_existing_pypi
        and (reusable_draft or defer_draft_validation),
    )
    validate_version_absent(
        "TestPyPI",
        version,
        _http_status(f"https://test.pypi.org/pypi/watcherobot/{version}/json"),
        allow_existing=reusable_draft or defer_draft_validation,
    )
    return ReleaseGateResult(version=version, reuse_artifact=reusable_draft)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--version-file", type=Path)
    parser.add_argument(
        "--allow-existing-pypi",
        action="store_true",
        help="Allow an existing PyPI version only while recovering a matching draft Release.",
    )
    parser.add_argument(
        "--defer-draft-validation",
        action="store_true",
        help="Defer draft Release access to a separate least-privilege workflow job.",
    )
    args = parser.parse_args()
    result = validate_gate(
        repository=args.repository,
        tag=args.tag,
        sha=args.sha,
        version_file=args.version_file,
        allow_existing_pypi=args.allow_existing_pypi,
        defer_draft_validation=args.defer_draft_validation,
    )
    print(json.dumps({"version": result.version, "reuse_artifact": result.reuse_artifact}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
