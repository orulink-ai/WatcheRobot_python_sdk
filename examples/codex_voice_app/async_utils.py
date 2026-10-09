"""Drain an already-owned operation even if its caller is cancelled again."""
from __future__ import annotations

import asyncio


async def drain_owned(task):
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            # SDK worker threads don't stop with asyncio cancellation. Preserve
            # the wrapper so the caller can still release a late resource lease.
            continue
