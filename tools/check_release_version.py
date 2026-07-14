"""Validate that a GitHub Release tag matches the Python package version."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DEFAULT_VERSION_FILE = Path(__file__).resolve().parents[1] / "src" / "watcherobot" / "__init__.py"


def read_package_version(path: Path = _DEFAULT_VERSION_FILE) -> str:
    match = _VERSION_PATTERN.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"package version not found in {path}")
    return match.group(1)


def validate_release_tag(tag: str, version: str) -> str:
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ValueError(
            f"release tag {tag!r} must match package version {version!r} as {expected_tag!r}"
        )
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="GitHub Release tag, for example v0.1.0a2")
    parser.add_argument("--version-file", type=Path, default=_DEFAULT_VERSION_FILE)
    args = parser.parse_args()

    version = validate_release_tag(args.tag, read_package_version(args.version_file))
    print(f"release tag matches watcherobot {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
