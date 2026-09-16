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
def reference_lock(*, timeout: float = 120) -> Iterator[None]:
    with operation_lock(
        default_runtime_instance_root() / "cleanup-coordination", timeout=timeout
    ):
        yield


def register_reference(path: Path, *, store: bool = False) -> None:
    """Persist project/store roots before creating environments or launching apps."""
    with reference_lock():
        _register_reference(path, store=store)


def register_environment(executable: Path) -> None:
    """Track a venv without recursively scanning a global Python installation."""
    root = _environment_root(executable)
    if root is not None:
        register_reference(root)


def register_application_reference(application_dir: Path, executable: Path) -> None:
    """Atomically protect an Application and its external virtual environment."""
    application_dir = application_dir.resolve(strict=True)
    executable = executable.absolute()
    executable.resolve(strict=True)
    environment_root = _environment_root(executable)
    with reference_lock():
        _register_reference(application_dir)
        if environment_root is not None:
            _register_reference(environment_root)
        # Recheck resources while GC is excluded. Once the records are visible,
        # later collection sees both roots as one committed launch reference.
        if not application_dir.is_dir() or not executable.is_file():
            raise FileNotFoundError("Application launch resources changed during registration")
        if environment_root is not None and not (environment_root / "pyvenv.cfg").is_file():
            raise FileNotFoundError("Application virtual environment changed during registration")


def _environment_root(executable: Path) -> Path | None:
    absolute = executable.absolute()
    for root in (absolute.parent, absolute.parent.parent):
        if (root / "pyvenv.cfg").is_file():
            return root.resolve()
    return None


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


def _launcher_paths(path: Path) -> list[Path]:
    from .manager import read_launcher

    command, _ = read_launcher(path)
    result = [Path(value) for value in command if Path(value).is_absolute()]
    if not result:
        raise ValueError("Shared Runtime launcher has no absolute path: " + str(path))
    return result


def _install_paths(path: Path) -> list[Path]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid Application install record: " + str(path))
    runtime = value.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("Application install record has no Runtime: " + str(path))
    root = runtime.get("root")
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise ValueError("Application install Runtime root is invalid: " + str(path))
    return [Path(root)]


def _venv_paths(path: Path) -> list[Path]:
    result: list[Path] = []
    home: Path | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        candidate = Path(value.strip())
        if separator and candidate.is_absolute():
            result.append(candidate)
            if key.strip().lower() == "home":
                home = candidate
    if home is None:
        raise ValueError("Virtual environment record has no absolute home: " + str(path))
    return result


def _reference_records(root: Path) -> list[Path]:
    """Enumerate reference records without treating access failures as emptiness."""
    if not root.exists():
        return []
    if not root.is_dir():
        raise NotADirectoryError(
            "Runtime reference root is not a directory: " + str(root)
        )
    with os.scandir(root) as entries:
        return sorted(
            Path(entry.path)
            for entry in entries
            if entry.name.endswith(".json")
            and entry.is_file(follow_symlinks=False)
        )


def _inventory() -> tuple[list[Path], list[Path]]:
    instance = default_runtime_instance_root()
    repositories = [instance / "bundles"]
    references: list[Path] = []
    for name in ("current-launcher.json", "previous-launcher.json"):
        pointer = instance / name
        if pointer.exists():
            references.extend(_launcher_paths(pointer))
    roots: list[Path] = []
    for record in _reference_records(instance / "runtime-references"):
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
                references.extend(_install_paths(base / "install.json"))
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
        with operation_lock(timeout=0), reference_lock(timeout=0):
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
                with operation_lock(root, timeout=0):
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
