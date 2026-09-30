"""Metadata receipts for previously content-verified immutable bundles.

Receipts are an I/O cache, not an authenticity/signature boundary. Every lookup
enumerates the tree again. Unsupported metadata always falls back to hashing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from collections.abc import Callable
from typing import Any


@lru_cache(maxsize=1)
def _windows_api() -> tuple[Any, Any, Any]:
    if sys.platform != "win32":
        raise OSError("Windows file metadata APIs are unavailable on this platform")
    import ctypes
    from ctypes import wintypes

    class BasicInfo(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_longlong)
            for name in ("creation", "access", "write", "change")
        ] + [("attributes", wintypes.DWORD)]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    api.GetFileInformationByHandleEx.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return ctypes, api, BasicInfo


def _change_time(path: Path, metadata: os.stat_result) -> int:
    if os.name != "nt":
        return metadata.st_ctime_ns
    # Python's Windows st_ctime is creation time, not the NTFS change timestamp.
    # Get the latter so restoring mtime after a same-size edit cannot hit cache.
    ctypes, api, basic_info = _windows_api()
    # READ_ATTRIBUTES; share read/write/delete; OPEN_EXISTING;
    # BACKUP_SEMANTICS | OPEN_REPARSE_POINT (inspect the link, not its target).
    handle = api.CreateFileW(str(path), 0x80, 0x7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = basic_info()
        if not api.GetFileInformationByHandleEx(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.change <= 0:
            raise OSError("Filesystem has no reliable change timestamp")
        return info.change
    finally:
        api.CloseHandle(handle)


def tree_stamp(root: Path) -> str | None:
    """Cover names, identity, content-change times, link targets and permissions."""
    digest = hashlib.sha256(b"watcher-bundle-metadata-v1\0")
    try:
        for path in sorted(root.rglob("*")):
            metadata = path.lstat()
            is_link = stat.S_ISLNK(metadata.st_mode) or bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            if not is_link and stat.S_ISDIR(metadata.st_mode):
                continue
            if not is_link and path.suffix.lower() in {".pyc", ".pyo"}:
                continue
            record = [
                path.relative_to(root).as_posix(), metadata.st_dev, metadata.st_ino,
                metadata.st_size, metadata.st_mtime_ns, _change_time(path, metadata),
                metadata.st_mode, os.readlink(path) if is_link else None,
            ]
            digest.update(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode())
            digest.update(b"\0")
        return digest.hexdigest()
    except (OSError, NotImplementedError):
        return None


def _receipt_path(root: Path, cache: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()
    return cache / (key + ".json")


def remember(root: Path, cache: Path, identity: str, stamp: str | None) -> None:
    if stamp is None:
        return
    try:
        cache.mkdir(parents=True, exist_ok=True)
        target = _receipt_path(root, cache)
        # Manager and service may verify the same immutable tree concurrently.
        temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(
            json.dumps({"schema": 1, "stamp": stamp, "digest": identity}),
            encoding="utf-8",
        )
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        # A cache write failure must not invalidate a successfully verified bundle.
        pass


def verified_digest(
    root: Path, cache: Path, compute: Callable[[Path], str]
) -> tuple[str, str | None]:
    before = tree_stamp(root)
    if before is not None:
        try:
            receipt = json.loads(_receipt_path(root, cache).read_text(encoding="utf-8"))
            if (
                isinstance(receipt, dict)
                and receipt.get("schema") == 1
                and receipt.get("stamp") == before
                and isinstance(receipt.get("digest"), str)
                and re.fullmatch(r"[0-9a-f]{64}", receipt["digest"])
            ):
                return receipt["digest"], before
        except (OSError, ValueError):
            pass
    identity = compute(root)
    after = tree_stamp(root) if before is not None else None
    if before is not None and after != before:
        raise ValueError("Runtime bundle changed during verification")
    remember(root, cache, identity, after)
    return identity, after
