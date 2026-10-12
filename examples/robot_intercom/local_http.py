"""Keep browser permissions on a stable loopback origin across app restarts."""
from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path


def open_listener(state_file: Path) -> socket.socket:
    state_file = Path(state_file)
    preferred = 0
    try:
        value = json.loads(state_file.read_text(encoding="utf-8")).get("port")
        if type(value) is int and 1024 <= value <= 65535:
            preferred = value
    except (OSError, ValueError, AttributeError):
        pass

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            listener.bind(("127.0.0.1", preferred))
        except OSError:
            if not preferred:
                raise
            listener.close()
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        if not preferred:
            temporary = state_file.with_name(f"{state_file.name}.{uuid.uuid4().hex}.tmp")
            try:
                state_file.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps({"port": listener.getsockname()[1]}), encoding="utf-8")
                temporary.replace(state_file)
            finally:
                temporary.unlink(missing_ok=True)
        return listener
    except BaseException:
        listener.close()
        raise
