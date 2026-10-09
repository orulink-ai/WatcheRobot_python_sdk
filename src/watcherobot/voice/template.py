"""Copy the packaged voice Application project into the initialization staging area."""
from __future__ import annotations

from pathlib import Path

from watcherobot.application.templates import copy_template_files


def write_voice_template(root: Path) -> None:
    """The initializer owns atomic creation; this function supplies voice assets."""
    copy_template_files("voice", root)
    credentials = root / 'credentials'
    credentials.mkdir(exist_ok=True)
    for kind in ('asr', 'llm', 'tts'):
        path = credentials / f'{kind}.toml'
        # Do not overwrite credentials even if called again by an extension.
        if not path.exists():
            path.write_bytes((root / 'credential-examples' / path.name).read_bytes())
