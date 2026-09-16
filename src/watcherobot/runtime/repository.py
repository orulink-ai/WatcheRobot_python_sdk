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


def _running_on_windows() -> bool:
    return os.name == "nt"


def _windows_extended_path(path: Path) -> str | Path:
    if not _running_on_windows():
        return path
    resolved = str(path.resolve())
    extended_prefix = chr(92) * 2 + "?" + chr(92)
    if resolved.startswith(extended_prefix):
        return resolved
    if resolved.startswith("\\"):
        return extended_prefix + "UNC" + chr(92) + resolved[2:]
    return extended_prefix + resolved


def _copy_bundle(source: Path, destination: Path) -> None:
    shutil.copytree(
        _windows_extended_path(source),
        _windows_extended_path(destination),
        symlinks=True,
    )


def _remove_bundle(path: Path) -> None:
    shutil.rmtree(_windows_extended_path(path))


def _published_path(path: Path) -> Path:
    return Path(_windows_extended_path(path))


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
    """Hash names, executable semantics and bytes of one Runtime bundle."""
    root = Path(_windows_extended_path(root))
    digest = hashlib.sha256(b"watcher-runtime-bundle-v2\0")
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
            if path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            name = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            executable = path.stat().st_mode & 0o111 if not _running_on_windows() else 0
            digest.update(executable.to_bytes(2, "big"))
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
        published_target = _published_path(target)
        if published_target.exists():
            if bundle_digest(published_target) != identity:
                raise ValueError("Published Runtime integrity check failed")
            os.utime(published_target, None)
            return published_target
        staging = root / (".staging-" + uuid.uuid4().hex)
        published_staging = _published_path(staging)
        try:
            _copy_bundle(source, staging)
            if bundle_digest(published_staging) != identity:
                raise ValueError("Staged Runtime integrity check failed")
            # copytree preserves source timestamps; grace must start at publication.
            os.utime(published_staging, None)
            os.replace(published_staging, published_target)
        finally:
            if published_staging.exists():
                _remove_bundle(staging)
        return published_target
