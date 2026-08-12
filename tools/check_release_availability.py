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


def validate_absent(version: str, *, pypi_status: int, testpypi_status: int, release_exists: bool) -> None:
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
    if existing:
        raise ValueError(f"watcherobot {version} already exists on {', '.join(existing)}")


def check(version: str, repository: str) -> None:
    release = subprocess.run(
        ["gh", "release", "view", f"v{version}", "--repo", repository, "--json", "tagName"],
        text=True,
        capture_output=True,
    )
    validate_absent(
        version,
        pypi_status=http_status(f"https://pypi.org/pypi/watcherobot/{version}/json"),
        testpypi_status=http_status(f"https://test.pypi.org/pypi/watcherobot/{version}/json"),
        release_exists=release.returncode == 0,
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
