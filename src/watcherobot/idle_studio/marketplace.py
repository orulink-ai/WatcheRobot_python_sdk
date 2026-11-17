"""Export a standalone Application snapshot from the canonical editor source."""
from __future__ import annotations

from pathlib import Path
import shutil


def export_application(target: Path) -> Path:
    """Create a new source root; never overwrite an existing user directory."""
    target = Path(target).resolve()
    source = Path(__file__).resolve().parent
    target.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copytree(source / "application", target, dirs_exist_ok=True)
        shutil.copytree(source, target / "idle_studio", ignore=shutil.ignore_patterns(
            "application", "__pycache__", "*.pyc", "marketplace.py", "design-proposal.html",
        ))
    except BaseException:
        shutil.rmtree(target)
        raise
    return target
