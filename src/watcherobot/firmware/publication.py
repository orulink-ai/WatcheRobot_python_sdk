"""Publication policy, separate from local inspection and application updates."""

from pathlib import Path

from .contract import FirmwareError
from .installation import Installation, inspect_installation


def inspect_publication(path: Path) -> Installation:
    """Require the author's complete layout and write plan for a new release."""
    info = inspect_installation(path, require_layout=True)
    if info.mode != "full":
        raise FirmwareError(
            "Publishing requires a complete installation package with bootloader, "
            "partition table and application images in the write segments. "
            "Prepare the complete build directory or build ZIP with its generated "
            "flash file mapping; an application-update package is only for an "
            "existing compatible layout. Custom partition layouts are supported."
        )
    return info
