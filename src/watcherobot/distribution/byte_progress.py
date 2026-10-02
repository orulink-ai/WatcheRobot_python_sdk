"""Adapt the public HF byte progress hook without printing or changing global state."""
from __future__ import annotations

from threading import RLock
from time import monotonic
from typing import Any

from huggingface_hub.utils import tqdm

from .ports import SnapshotProgress


def snapshot_progress_bar(total: int | None, callback: SnapshotProgress) -> type[tqdm]:
    """Use one fixed denominator, ignoring the file-count and transfer-only bars.

    HF reports reconstructed source bytes separately from compressed Xet wire
    bytes. Only source bytes have the same units as the snapshot file sizes.
    Negative updates mean an HTTP resume was rejected and bytes were discarded.
    """
    lock = RLock()
    downloaded = 0
    attempt = 0
    last_emitted: float | None = None

    class SnapshotProgressBar(tqdm):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            name = kwargs.pop("name", None)
            self._source_bytes = (
                kwargs.get("unit") == "B"
                and name == "huggingface_hub.snapshot_download"
            )
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)

        def update(self, n: float | None = 1) -> None:
            nonlocal downloaded, attempt, last_emitted
            if not self._source_bytes or n is None or n == 0:
                return
            with lock:
                if n < 0:
                    attempt += 1
                downloaded = max(0, downloaded + int(n))
                # Never replace an unknown total with the live, partial HF total.
                now = monotonic()
                if (last_emitted is None or n < 0 or downloaded == total
                        or now - last_emitted >= 0.1):
                    callback(downloaded, total, attempt)
                    last_emitted = now

    return SnapshotProgressBar
