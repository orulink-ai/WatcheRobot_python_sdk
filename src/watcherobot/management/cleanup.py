"""Delete only SDK-owned unreferenced candidates; never application environments."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from watcherobot.distribution.install import ApplicationEnvironmentRunner

from watcherobot.runtime.cleanup import _is_link, reference_lock
from watcherobot.runtime.repository import operation_lock

from .state import ManagerStore


def process_references() -> list[Path] | None:
    from watcherobot.runtime.cleanup import process_references as inspect

    if sys.platform != "darwin":
        return inspect()
    # psutil has no memory_maps on macOS. lsof includes mappings, executable,
    # open files and cwd; fail closed if the current user's scan is incomplete.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(
                ["/usr/sbin/lsof", "-nP", "-a", "-u", str(os.getuid()), "-Fpn"],
                stdout=output,
                stderr=errors,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0 or errors.tell() or output.tell() > 16 * 1024 * 1024:
            return None
        output.seek(0)
        return [
            Path(line[1:].rstrip(b"\n").decode("utf-8", errors="strict"))
            for line in output
            if line.startswith(b"n/")
        ]


def clean_environments(
    store: ManagerStore,
    references: Callable[[], list[Path] | None] = process_references,
) -> dict[str, object]:
    report: dict[str, object] = {"removed": 0, "deferred": False, "errors": []}
    with operation_lock(timeout=120), reference_lock():
        from watcherobot.runtime.cleanup import _inventory

        refs = references()
        if refs is None:
            report["deferred"] = True
            return report
        _, persistent = _inventory()
        refs.extend(persistent)
        state = store.snapshot()
        for key in ("selection", "previous", "candidate"):
            item = state.get(key)
            if item and item.get("root"):
                refs.append(Path(item["root"]))
        if state.get("transaction"):
            report["deferred"] = True
            return report
        root = store.root / "environments"
        if not root.exists():
            return report
        if _is_link(root):
            raise ValueError("SDK environment repository must not be a link")
        errors: list[str] = []
        removed = 0
        for entry in root.iterdir():
            if (
                not re.fullmatch("[0-9a-f]{64}", entry.name)
                or _is_link(entry)
                or not entry.is_dir()
            ):
                continue
            if any(path == entry or path.is_relative_to(entry) for path in refs):
                continue
            try:
                shutil.rmtree(entry)
                removed += 1
            except OSError as exc:
                errors.append(f"{entry.name}: {type(exc).__name__}")
        report.update(removed=removed, errors=errors)
    return report


def retire_previous(
    store: ManagerStore,
    *,
    now: float,
    before_update: bool = False,
    references: Callable[[], list[Path] | None] = process_references,
) -> bool:
    """Expire one rollback record only after process visibility is sufficient."""
    from watcherobot.runtime.cleanup import _launcher_paths
    from watcherobot.runtime.daemon.instance import default_runtime_instance_root

    with operation_lock(timeout=120), reference_lock():
        state = store.snapshot()
        previous = state.get("previous")
        if not previous or state.get("transaction"):
            return False
        if not before_update and now - previous.get("retained_at", now) < 7 * 24 * 3600:
            return False
        refs = references()
        if refs is None:
            return False
        root = Path(previous["root"]) if previous.get("root") else None
        if root and any(path == root or path.is_relative_to(root) for path in refs):
            return False
        backup = default_runtime_instance_root() / "previous-launcher.json"
        if root and backup.is_file():
            paths = _launcher_paths(backup)
            if any(p == root or p.is_relative_to(root) for p in paths):
                backup.unlink()
        state["previous"] = None
        store.save(state)
        return True


def clean_downloads(store: ManagerStore) -> dict[str, object]:
    """Discard transaction downloads; never hide deletion failures or follow roots."""
    root = store.root / "downloads"
    if not os.path.lexists(root):
        return {"deferred": False, "errors": []}
    if _is_link(root):
        return {"deferred": True, "errors": ["download_root_is_link"]}
    try:
        if root.exists():
            shutil.rmtree(root)
    except OSError as exc:
        return {"deferred": True, "errors": [type(exc).__name__]}
    return {"deferred": False, "errors": []}


def clean_cache(
    store: ManagerStore, uv: Path, *, runner: ApplicationEnvironmentRunner | None = None
) -> dict[str, object]:
    """Prune only SDK-private uv objects using uv's own in-use protection."""
    from watcherobot.distribution.install import (
        ApplicationEnvironmentCommand,
        SystemApplicationEnvironmentRunner,
    )

    root = store.root / "cache"
    if not os.path.lexists(root):
        return {"deferred": False, "errors": []}
    if _is_link(root):
        return {"deferred": True, "errors": ["cache_root_is_link"]}
    if not root.exists():
        return {"deferred": False, "errors": []}
    try:
        (runner or SystemApplicationEnvironmentRunner()).run(
            ApplicationEnvironmentCommand(
                "cleaning_sdk_cache",
                uv,
                (
                    "--no-config",
                    "--offline",
                    "cache",
                    "prune",
                    "--ci",
                    "--cache-dir",
                    str(root),
                ),
                store.root,
                store.root,
                (("UV_CACHE_DIR", str(root)),),
            )
        )
    except Exception as exc:  # noqa: BLE001 -- cleanup reports failure without undoing a committed update
        return {"deferred": True, "errors": [type(exc).__name__]}
    return {"deferred": False, "errors": []}
