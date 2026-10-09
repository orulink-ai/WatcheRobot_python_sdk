"""Cooperative cancellation shared by publication and activation."""

import os
from pathlib import Path


class RuntimeActivationCancelled(RuntimeError):
    """The requesting launcher cancelled its pending operation."""


def check_cancelled() -> None:
    marker = os.environ.get("WATCHER_RUNTIME_CANCEL_FILE")
    if marker and Path(marker).exists():
        raise RuntimeActivationCancelled("Runtime activation cancelled by launcher")
