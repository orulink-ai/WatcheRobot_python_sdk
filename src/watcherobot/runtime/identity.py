"""Content identity, independent of checkout location and package version labels."""

from __future__ import annotations

import hashlib
import sys
from functools import lru_cache
from pathlib import Path

from watcherobot import __version__

_IGNORED_DIRECTORY_NAMES = {"__pycache__"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}
_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_TEXT_FILE_NAMES = {"py.typed"}


def source_build_id(root: Path) -> str:
    """Hash packaged source and resources without including generated caches."""
    digest = hashlib.sha256(b"watcher-sdk-source-v2\0")
    paths = (
        path
        for path in root.rglob("*")
        if path.is_file()
        and not _IGNORED_DIRECTORY_NAMES.intersection(path.relative_to(root).parts)
        and path.suffix.lower() not in _IGNORED_SUFFIXES
    )
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        if path.suffix.lower() in _TEXT_SUFFIXES or path.name in _TEXT_FILE_NAMES:
            content = content.replace(b"\r\n", b"\n")
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def runtime_identity() -> dict[str, str]:
    if getattr(sys, "frozen", False):
        # A published bundle is named by its complete verified content hash.
        root = Path(sys.executable).resolve().parent
        from .repository import bundle_digest

        build = "bundle:" + bundle_digest(root)
    else:
        build = "source:" + source_build_id(Path(__file__).resolve().parents[1])
    return {"sdk_version": __version__, "build_id": build}
