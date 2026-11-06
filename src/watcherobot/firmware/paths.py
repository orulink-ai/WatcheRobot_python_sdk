"""Native absolute paths without relying on the Windows long-path policy."""

import os
from pathlib import Path


def filesystem_path(path: Path) -> Path:
    value = os.path.abspath(os.path.expanduser(str(path)))
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = (
            "\\\\?\\UNC\\" + value[2:]
            if value.startswith("\\\\")
            else "\\\\?\\" + value
        )
    return Path(value)
