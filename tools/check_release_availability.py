"""Reject a release version that already exists on any immutable release surface."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request

from packaging.version import Version


def http_status(url: str) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "watcherobot-release-availability/1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def validate_absent(
    version: str,
    *,
    pypi_status: int,
    testpypi_status: int,
    release_exists: bool,
    tag_exists: bool,
) -> None:
    canonical = str(Version(version))
    if canonical != version:
        raise ValueError(f"version {version!r} is not canonical PEP 440 ({canonical!r})")
    existing = []
    if pypi_status != 404:
        existing.append("PyPI")
    if testpypi_status != 404:
        existing.append("TestPyPI")
    if release_exists:
        existing.append("GitHub Release")
    if tag_exists:
        existing.append("Git tag")
    if existing:
        raise ValueError(f"watcherobot {version} already exists on {', '.join(existing)}")


def github_resource_exists(repository: str, resource: str) -> bool:
    """Return False only for a verified GitHub 404; fail closed otherwise."""

    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/{resource}"],
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        return True
    diagnostic = f"{result.stdout}\n{result.stderr}"
    if "HTTP 404" in diagnostic:
        return False
    raise RuntimeError(f"GitHub lookup failed for {resource}: {diagnostic.strip()}")


def check(version: str, repository: str) -> None:
    tag = f"v{version}"
    validate_absent(
        version,
        pypi_status=http_status(f"https://pypi.org/pypi/watcherobot/{version}/json"),
        testpypi_status=http_status(f"https://test.pypi.org/pypi/watcherobot/{version}/json"),
        release_exists=github_resource_exists(repository, f"releases/tags/{tag}"),
        tag_exists=github_resource_exists(repository, f"git/ref/tags/{tag}"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    check(args.version, args.repository)
    print(json.dumps({"version": args.version, "available": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
