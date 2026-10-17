"""SDK-only transactional activation under the existing shared lifecycle lock."""

from __future__ import annotations

import time
from typing import Any, Protocol

from watcherobot.runtime.repository import operation_lock

from .activity import ActivityRegistry
from .state import ManagerStore


class Runtime(Protocol):
    def status(self) -> dict[str, Any] | None: ...
    def start(self, target: dict[str, Any]) -> None: ...


def _build(status: dict[str, Any] | None) -> str | None:
    return status.get("runtime", {}).get("build_id") if status else None


def activate(
    store: ManagerStore,
    target: dict[str, Any],
    fallback: dict[str, Any],
    runtime: Runtime,
) -> None:
    """Caller owns the SDK transaction lock; manager core owns no nested locks."""
    with ActivityRegistry().switch(), operation_lock(timeout=120):
        state = store.snapshot()
        before = state["selection"] or fallback
        if target["contracts"]["state"] != before["contracts"]["state"]:
            raise ValueError(
                "SDK data schema migration is not reversible; update is blocked"
            )
        status = runtime.status()
        if status and status.get("application", {}).get("state") in {
            "running",
            "starting",
            "stopping",
        }:
            raise RuntimeError("Application is running; wait before changing SDK")
        state["transaction"] = {"before": before, "target": target}
        store.save(state)
        try:
            runtime.start(target)
            if _build(runtime.status()) != target["build_id"]:
                raise RuntimeError(
                    "Another SDK took over before activation was committed"
                )
        except Exception:
            # Existing SDK activation core already attempts to restore its last
            # launcher. Retain a transaction when that restoration did not finish.
            if _build(runtime.status()) == before["build_id"]:
                state["transaction"] = None
                store.save(state)
            raise
        state["selection"] = target
        state["previous"] = {**before, "retained_at": time.time()}
        state["candidate"] = None
        state["transaction"] = None
        store.save(state)


def recover(store: ManagerStore, runtime: Runtime) -> str:
    """Never reclaim an instance that a later explicit external SDK owns."""
    with ActivityRegistry().switch(), operation_lock(timeout=120):
        state = store.snapshot()
        transaction = state.get("transaction")
        if not transaction:
            return "no_transaction"
        before, target = transaction["before"], transaction["target"]
        live = _build(runtime.status())
        if live == target["build_id"]:
            state["selection"] = target
            state["previous"] = {**before, "retained_at": time.time()}
            state["candidate"] = None
            outcome = "completed"
        elif live is not None and live != before["build_id"]:
            outcome = "external_takeover"
        else:
            if live is None:
                runtime.start(before)
                if _build(runtime.status()) != before["build_id"]:
                    raise RuntimeError(
                        "SDK recovery did not restore the previous build"
                    )
            outcome = "restored"
        state["transaction"] = None
        store.save(state)
        return outcome
