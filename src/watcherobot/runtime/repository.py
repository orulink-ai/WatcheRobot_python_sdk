"""SDK-owned immutable Runtime storage and cross-process lifecycle operations.

Published directories are never moved or repaired in place. Conservative collection
in runtime.cleanup retains running executables, rollback and application references.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from .daemon.instance import (
    RuntimeAlreadyRunningError,
    RuntimeInstanceLock,
    default_runtime_instance_root,
)


@contextmanager
def operation_lock(root: Path | None = None, *, timeout: float = 30) -> Iterator[None]:
    """Serialize short-lived managers, independently of the Daemon lifetime lock."""
    lock = RuntimeInstanceLock(
        (root or default_runtime_instance_root()) / "operation.lock"
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock.acquire()
            break
        except RuntimeAlreadyRunningError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def bundle_digest(root: Path) -> str:
    """Hash names and bytes, preserving only internal relative symlinks."""
    digest = hashlib.sha256(b"watcher-runtime-bundle-v1\0")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or not path.resolve(
                strict=True
            ).is_relative_to(root.resolve()):
                raise ValueError("Runtime bundle links must stay inside the bundle")
            name = path.relative_to(root).as_posix().encode("utf-8")
            value = target.encode("utf-8")
            digest.update(
                b"link"
                + len(name).to_bytes(8, "big")
                + name
                + len(value).to_bytes(8, "big")
                + value
            )
            continue
        if path.is_file():
            name = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
    return digest.hexdigest()


def prepare_bundle(source: Path, repository: Path | None = None) -> Path:
    """Verify and publish an immutable candidate; never activate or stop anything."""
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("Runtime bundle must be a directory")
    root = repository or default_runtime_instance_root() / "bundles"
    root = root.resolve()
    if root == source or root.is_relative_to(source):
        raise ValueError("Runtime repository must be outside its source")
    with operation_lock(root):
        identity = bundle_digest(source)
        target = root / identity
        if target.exists():
            if bundle_digest(target) != identity:
                raise ValueError("Published Runtime integrity check failed")
            os.utime(target, None)
            return target
        staging = root / (".staging-" + uuid.uuid4().hex)
        try:
            shutil.copytree(source, staging, symlinks=True)
            if bundle_digest(staging) != identity:
                raise ValueError("Staged Runtime integrity check failed")
            # copytree preserves source timestamps; grace must start at publication.
            os.utime(staging, None)
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return target
