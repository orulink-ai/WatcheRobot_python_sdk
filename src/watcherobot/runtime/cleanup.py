"""Conservative collection of SDK-owned, immutable Runtime directories.

Never delete installer directories, user data, applications, or legacy layouts.
Missing process visibility or unreadable reference metadata blocks collection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar, cast

import psutil

GRACE_SECONDS = 7 * 24 * 60 * 60
F = TypeVar("F", bound=Callable[..., Any])


def default_runtime_instance_root() -> Path:
    from .daemon.instance import default_runtime_instance_root as resolve

    return resolve()


@contextmanager
def operation_lock(root: Path | None = None, *, timeout: float = 30) -> Iterator[None]:
    from .repository import operation_lock as acquire

    with acquire(root, timeout=timeout):
        yield


def cleanup_after(function: F) -> F:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = function(*args, **kwargs)
        collect_runtime_garbage()
        return result

    return cast(F, wrapped)


@contextmanager
def reference_lock() -> Iterator[None]:
    with operation_lock(
        default_runtime_instance_root() / "cleanup-coordination", timeout=120
    ):
        yield


def register_reference(path: Path, *, store: bool = False) -> None:
    """Persist project/store roots before creating environments or launching apps."""
    with reference_lock():
        _register_reference(path, store=store)


def register_environment(executable: Path) -> None:
    """Track a venv without recursively scanning a global Python installation."""
    for root in (executable.absolute().parent, executable.absolute().parent.parent):
        if (root / "pyvenv.cfg").is_file():
            register_reference(root)
            return


def _is_link(path: Path) -> bool:
    metadata = path.lstat()
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _register_reference(path: Path, *, store: bool = False) -> None:
    path = path.resolve()
    if not store and any(
        parent.parent.name in ("bundles", "runtimes")
        and re.fullmatch(r"[0-9a-f]{64}", parent.name)
        for parent in (path, *path.parents)
    ):
        return
    root = default_runtime_instance_root() / "runtime-references"
    root.mkdir(parents=True, exist_ok=True)
    resolved = str(path.resolve())
    key = hashlib.sha256(os.path.normcase(resolved).encode()).hexdigest()
    target = root / (key + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"path": resolved, "store": store}), encoding="utf-8"
    )
    temporary.replace(target)


def store_operation(function: F) -> F:
    """Prevent cleanup racing environment creation before install.json exists."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with reference_lock():
            _register_reference(Path(kwargs["store_root"]), store=True)
            result = function(*args, **kwargs)
        collect_runtime_garbage()
        return result

    return cast(F, wrapped)


def process_references() -> list[Path] | None:
    """Inspect this user's executable, arguments, cwd, open files and mappings."""
    paths: list[Path] = []
    username = psutil.Process().username()
    for process in psutil.process_iter():
        if os.name == "nt" and process.pid in (0, 4):
            continue  # Windows kernel/idle processes cannot execute user bundles.
        try:
            if process.username() != username:
                continue
            values = [process.exe(), process.cwd(), *process.cmdline()]
            values.extend(item.path for item in process.open_files())
            values.extend(item.path for item in process.memory_maps())
            paths.extend(Path(value) for value in values if Path(value).is_absolute())
            executable = Path(process.exe())
            for root in (executable.parent, executable.parent.parent):
                if (root / "pyvenv.cfg").is_file():
                    paths.extend(_venv_paths(root / "pyvenv.cfg"))
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError, NotImplementedError):
            return None
    return paths


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _json_paths(path: Path) -> list[Path]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid reference document: " + str(path))
    return [Path(s) for s in _strings(value) if Path(s).is_absolute()]


def _venv_paths(path: Path) -> list[Path]:
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        _, separator, value = line.partition("=")
        if separator and Path(value.strip()).is_absolute():
            result.append(Path(value.strip()))
    return result


def _inventory() -> tuple[list[Path], list[Path]]:
    instance = default_runtime_instance_root()
    repositories = [instance / "bundles"]
    references: list[Path] = []
    for name in ("current-launcher.json", "previous-launcher.json"):
        pointer = instance / name
        if pointer.exists():
            references.extend(_json_paths(pointer))
    roots: list[Path] = []
    for record in (instance / "runtime-references").glob("*.json"):
        item = json.loads(record.read_text(encoding="utf-8"))
        path = Path(item["path"])
        if not path.is_absolute() or not isinstance(item["store"], bool):
            raise ValueError("Invalid Runtime reference")
        if item["store"]:
            repositories.append(path / "runtimes")
            roots.extend(path / name for name in ("apps", "trash", "staging"))
        else:
            roots.append(path)
        references.append(path)
    for root in roots:
        if not root.exists():
            continue

        # os.walk raises on inaccessible directories, unlike glob on some Python versions.
        def fail(error: OSError) -> None:
            raise error

        for directory, directories, files in os.walk(
            root, onerror=fail, followlinks=False
        ):
            base = Path(directory)
            directories[:] = [
                name
                for name in directories
                if name not in (".git", "node_modules", "__pycache__")
            ]
            if "install.json" in files:
                references.extend(_json_paths(base / "install.json"))
            if "pyvenv.cfg" in files:
                references.extend(_venv_paths(base / "pyvenv.cfg"))
            for name in [*files, *directories]:
                link = base / name
                if _is_link(link):
                    references.append(link.resolve(strict=True))
                    if name in directories:
                        directories.remove(name)
    return repositories, references


def collect_runtime_garbage() -> dict[str, Any]:
    """Best effort automatic GC; errors never turn a successful launch into failure."""
    report: dict[str, Any] = {"deleted": [], "skipped": [], "error": None}
    instance = default_runtime_instance_root()
    try:
        with operation_lock(timeout=0), reference_lock():
            repositories, references = _inventory()
            if not any(
                repository.is_dir()
                and not _is_link(repository)
                and any(
                    re.fullmatch(r"[0-9a-f]{64}", candidate.name)
                    and time.time() - candidate.lstat().st_mtime >= GRACE_SECONDS
                    for candidate in repository.iterdir()
                )
                for repository in repositories
            ):
                return report
            processes = process_references()
            if processes is None:
                raise ValueError("Process references unavailable; cleanup deferred")
            references.extend(processes)
            references = [p.resolve() for p in references]
            for repository in repositories:
                if not repository.exists() or _is_link(repository):
                    continue
                root = repository.resolve(strict=True)
                with operation_lock(root):
                    for candidate in root.iterdir():
                        if not re.fullmatch(r"[0-9a-f]{64}", candidate.name):
                            continue
                        # Do not traverse symlinks/junctions or delete outside the verified root.
                        if (
                            not candidate.is_dir()
                            or _is_link(candidate)
                            or candidate.resolve().parent != root
                        ):
                            continue
                        if time.time() - candidate.stat().st_mtime < GRACE_SECONDS:
                            continue
                        if any(
                            p == candidate or p.is_relative_to(candidate)
                            for p in references
                        ):
                            continue
                        try:
                            shutil.rmtree(candidate)
                            report["deleted"].append(str(candidate))
                        except OSError as exc:
                            report["skipped"].append(
                                {"path": str(candidate), "reason": str(exc)}
                            )
            instance.mkdir(parents=True, exist_ok=True)
            (instance / "cleanup-report.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        psutil.Error,
    ) as exc:
        report["error"] = str(exc)
        try:
            instance.mkdir(parents=True, exist_ok=True)
            (instance / "cleanup-report.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
        except OSError:
            pass
    return report
