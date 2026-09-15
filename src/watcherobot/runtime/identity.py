"""Content identity, independent of checkout location and package version labels."""

from __future__ import annotations

import hashlib
import sys
from functools import lru_cache
from pathlib import Path

from watcherobot import __version__


def source_build_id(root: Path) -> str:
    digest = hashlib.sha256(b"watcher-sdk-source-v1\0")
    for path in sorted(root.rglob("*.py")):
        name = path.relative_to(root).as_posix().encode()
        content = path.read_bytes().replace(b"\r\n", b"\n")
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
