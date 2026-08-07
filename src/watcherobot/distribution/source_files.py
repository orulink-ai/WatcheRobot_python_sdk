"""Deterministic source selection for Application checks and publishing."""

from __future__ import annotations

from pathlib import Path

from watcherobot.application.ignore import (
    APPLICATION_IGNORE_FILE,
    ApplicationIgnoreError,
    is_application_path_ignored,
    load_application_ignore_patterns,
)


_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".idea",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        ".vscode",
        "__pycache__",
        "build",
        "dist",
        "env",
        "venv",
    }
)
_EXCLUDED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "pip-selfcheck.json",
        "pyvenv.cfg",
    }
)
_EXCLUDED_FILE_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".pth",
    }
)


class ApplicationSourceError(RuntimeError):
    """Raised when source cannot be safely represented by a fixed snapshot."""

    code = "app_content_forbidden"


def collect_application_source_files(application_dir: Path) -> tuple[Path, ...]:
    """Return safe relative file paths for a future Space upload."""

    root = Path(application_dir).resolve()
    if not root.is_dir():
        raise ApplicationSourceError(
            f"Application directory does not exist: {root}"
        )
    try:
        ignore_patterns = load_application_ignore_patterns(root)
    except ApplicationIgnoreError as exc:
        raise ApplicationSourceError(str(exc)) from exc

    selected: list[Path] = []
    for candidate in root.rglob("*"):
        relative = candidate.relative_to(root)
        if relative.as_posix() == APPLICATION_IGNORE_FILE:
            continue
        if is_application_path_ignored(relative, ignore_patterns):
            continue
        if _is_excluded(relative, is_directory=candidate.is_dir()):
            continue
        if candidate.is_symlink():
            raise ApplicationSourceError(
                "Application source must not contain a symbolic link: "
                f"{relative.as_posix()}"
            )
        if candidate.is_file():
            selected.append(relative)
    return tuple(sorted(selected, key=lambda path: path.as_posix()))


def _is_excluded(relative: Path, *, is_directory: bool) -> bool:
    directory_parts = relative.parts if is_directory else relative.parts[:-1]
    if any(
        part in _EXCLUDED_DIRECTORY_NAMES or part.endswith(".egg-info")
        for part in directory_parts
    ):
        return True
    if is_directory:
        return False

    name = relative.name
    if name in _EXCLUDED_FILE_NAMES:
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    if name == ".env":
        return True
    return relative.suffix.lower() in _EXCLUDED_FILE_SUFFIXES
