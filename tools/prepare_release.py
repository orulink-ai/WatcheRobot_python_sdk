"""Deterministically prepare the watcherobot version source and changelog."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from check_release_version import (
    next_release_version,
    read_package_version,
    validate_version_increment,
)


_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_VERSION_FILE = _ROOT / "src" / "watcherobot" / "__init__.py"
_DEFAULT_CHANGELOG = _ROOT / "CHANGELOG.md"
_VERSION_ASSIGNMENT = re.compile(r'(?m)^__version__\s*=\s*["\'][^"\']+["\']$')


def prepare_release(
    *,
    version_file: Path,
    changelog_file: Path,
    target: str | None,
    release_type: str | None,
    source: str,
) -> str:
    if (target is None) == (release_type is None):
        raise ValueError("exactly one of target or release_type is required")
    current = read_package_version(version_file)
    version = (
        validate_version_increment(current, target)
        if target is not None
        else next_release_version(current, str(release_type))
    )

    changelog = changelog_file.read_text(encoding="utf-8") if changelog_file.exists() else "# 更新日志\n"
    heading = f"## [{version}]"
    if heading in changelog:
        raise ValueError(f"changelog entry for {version} already exists")
    version_text = version_file.read_text(encoding="utf-8")
    updated_version_text, count = _VERSION_ASSIGNMENT.subn(
        f'__version__ = "{version}"',
        version_text,
        count=1,
    )
    if count != 1:
        raise ValueError(f"package version assignment not found in {version_file}")

    entry = f"## [{version}] - 待发布\n\n- {source.strip()}\n\n"
    if changelog.startswith("# 更新日志"):
        first_line, remainder = changelog.split("\n", 1)
        updated_changelog = f"{first_line}\n\n{entry}{remainder.lstrip()}"
    else:
        updated_changelog = f"# 更新日志\n\n{entry}{changelog.lstrip()}"

    version_file.write_text(updated_version_text, encoding="utf-8")
    changelog_file.write_text(updated_changelog, encoding="utf-8")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--target")
    group.add_argument("--release-type", choices=("prerelease", "stable", "minor", "major"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--version-file", type=Path, default=_DEFAULT_VERSION_FILE)
    parser.add_argument("--changelog", type=Path, default=_DEFAULT_CHANGELOG)
    args = parser.parse_args()
    print(
        prepare_release(
            version_file=args.version_file,
            changelog_file=args.changelog,
            target=args.target,
            release_type=args.release_type,
            source=args.source,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
