"""Validate that a GitHub Release tag matches the Python package version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from packaging.version import InvalidVersion, Version


_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DEFAULT_VERSION_FILE = Path(__file__).resolve().parents[1] / "src" / "watcherobot" / "__init__.py"


def read_package_version(path: Path = _DEFAULT_VERSION_FILE) -> str:
    match = _VERSION_PATTERN.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"package version not found in {path}")
    return match.group(1)


def validate_release_tag(tag: str, version: str) -> str:
    validate_package_version(version)
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ValueError(
            f"release tag {tag!r} must match package version {version!r} as {expected_tag!r}"
        )
    return version


def validate_package_version(value: str) -> Version:
    """Return a canonical PEP 440 version or reject ambiguous spellings."""

    try:
        version = Version(value)
    except InvalidVersion as error:
        raise ValueError(f"version {value!r} is not valid PEP 440") from error
    if str(version) != value:
        raise ValueError(f"version {value!r} must use canonical PEP 440 spelling {str(version)!r}")
    return version


def next_release_version(current: str, release_type: str) -> str:
    """Calculate the next supported release without guessing user intent."""

    version = validate_package_version(current)
    if release_type == "prerelease":
        if version.pre and version.pre[0] == "a":
            return f"{version.major}.{version.minor}.{version.micro}a{version.pre[1] + 1}"
        if version.is_prerelease:
            raise ValueError("prerelease increment only supports the current alpha series")
        return f"{version.major}.{version.minor}.{version.micro + 1}a1"
    if release_type == "stable":
        if not version.is_prerelease:
            raise ValueError("stable release requires a pre-release version")
        return f"{version.major}.{version.minor}.{version.micro}"
    if release_type == "minor":
        return f"{version.major}.{version.minor + 1}.0a1"
    if release_type == "major":
        return f"{version.major + 1}.0.0a1"
    raise ValueError(f"unsupported release type {release_type!r}")


def validate_version_increment(current: str, target: str) -> str:
    current_version = validate_package_version(current)
    target_version = validate_package_version(target)
    if target_version <= current_version:
        raise ValueError(f"target version {target!r} must be newer than {current!r}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", help="Git tag, for example v0.1.0a2")
    parser.add_argument("--version-file", type=Path, default=_DEFAULT_VERSION_FILE)
    parser.add_argument("--current")
    parser.add_argument("--release-type", choices=("prerelease", "stable", "minor", "major"))
    parser.add_argument("--target")
    args = parser.parse_args()

    if args.release_type:
        if not args.current:
            parser.error("--release-type requires --current")
        print(next_release_version(args.current, args.release_type))
        return 0
    if args.target:
        if not args.current:
            parser.error("--target requires --current")
        print(validate_version_increment(args.current, args.target))
        return 0
    if not args.tag:
        parser.error("tag is required unless calculating a version")
    version = validate_release_tag(args.tag, read_package_version(args.version_file))
    print(f"release tag matches watcherobot {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
