"""Shared Application source ignore rules for validation and publishing."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath


APPLICATION_IGNORE_FILE = ".watcherignore"


class ApplicationIgnoreError(RuntimeError):
    """Raised when Application ignore rules cannot be loaded safely."""


def load_application_ignore_patterns(source: Path) -> tuple[str, ...]:
    """Load normalized non-comment patterns from one Application root."""

    ignore_path = Path(source) / APPLICATION_IGNORE_FILE
    if not ignore_path.is_file():
        return ()
    try:
        lines = ignore_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ApplicationIgnoreError(
            f"Cannot read {APPLICATION_IGNORE_FILE}: {exc}"
        ) from exc
    return tuple(
        line.strip().replace("\\", "/")
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )


def is_application_path_ignored(
    relative_path: Path,
    patterns: tuple[str, ...],
) -> bool:
    """Return whether a relative Application path matches ignore rules."""

    relative = PurePosixPath(relative_path.as_posix())
    for pattern in patterns:
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            ancestors = (
                "/".join(relative.parts[:depth])
                for depth in range(1, len(relative.parts))
            )
            if any(fnmatchcase(ancestor, prefix) for ancestor in ancestors):
                return True
            continue
        if relative.match(pattern):
            return True
    return False
