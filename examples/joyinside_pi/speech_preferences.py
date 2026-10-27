"""Per-user backend choice; no cloud credentials in application preferences."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

BACKENDS = {"joyinside", "doubao"}


def load_backend(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        name = value.get("speech_backend") if isinstance(value, dict) else None
        return name if isinstance(name, str) and name in BACKENDS else "joyinside"
    except (OSError, ValueError):
        return "joyinside"


def save_backend(path: Path, name: str) -> None:
    if name not in BACKENDS:
        raise ValueError("无效的语音后端")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps({"speech_backend": name}) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
