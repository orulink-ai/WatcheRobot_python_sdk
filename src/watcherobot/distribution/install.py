"""SDK-owned local installation of immutable Applications.

The Desktop supplies the locked Application Runtime bundle, but this module
owns every mutable App Store operation: Runtime publication, source delivery,
isolated environment creation, install records, inventory, and removal.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from .download import DownloadError, download_application_snapshot
from .events import ErrorCode, EventSink, ProgressEvent
from .ports import MarketplaceHubClient


_RUNTIME_MANIFEST = "runtime.json"
_RUNTIME_TREE_PREFIX = b"watcher-application-runtime-tree-sha256-v1\0"
_MAX_OUTPUT_BYTES = 1024 * 1024
_APPLICATION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_RUNTIME_SCHEMA_VERSION = 1


class ApplicationInstallError(RuntimeError):
    """A non-sensitive failure suitable for the distribution command boundary."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ApplicationEnvironmentCommand:
    """One argument-array environment command, never interpreted by a shell."""

    stage: str
    executable: Path
    arguments: tuple[str, ...]
    current_dir: Path
    environment_root: Path
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ApplicationEnvironmentOutput:
    stdout: bytes = b""
    stderr: bytes = b""


class ApplicationEnvironmentRunner(Protocol):
    """Run a bounded environment command."""

    def run(
        self,
        command: ApplicationEnvironmentCommand,
    ) -> ApplicationEnvironmentOutput: ...


class SystemApplicationEnvironmentRunner:
    """The production runner for the locked Python and uv Runtime resources."""

    def run(
        self,
        command: ApplicationEnvironmentCommand,
    ) -> ApplicationEnvironmentOutput:
        environment = dict(os.environ)
        for name in (
            "PYTHONHOME",
            "PYTHONPATH",
            "UV_PROJECT_ENVIRONMENT",
            "UV_PYTHON",
            "UV_PYTHON_INSTALL_DIR",
            "VIRTUAL_ENV",
        ):
            environment.pop(name, None)
        environment.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONUTF8": "1",
                "UV_NO_SYSTEM_CONFIG": "1",
                "UV_PYTHON_DOWNLOADS": "never",
                "UV_PYTHON_PREFERENCE": "only-system",
            }
        )
        environment.update(dict(command.environment))
        try:
            completed = subprocess.run(
                [str(command.executable), *command.arguments],
                cwd=command.current_dir,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ApplicationInstallError(
                ErrorCode.INTERNAL_ERROR,
                "Application environment command failed",
            ) from exc
        if (
            completed.returncode != 0
            or len(completed.stdout) > _MAX_OUTPUT_BYTES
            or len(completed.stderr) > _MAX_OUTPUT_BYTES
        ):
            raise ApplicationInstallError(
                ErrorCode.INTERNAL_ERROR,
                "Application environment command failed",
            )
        return ApplicationEnvironmentOutput(
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True)
class InstalledApplication:
    application_id: str
    name: str
    version: str
    status: str
    application_root: Path
    space_id: str = ""
    commit: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.application_id,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "application_root": str(self.application_root),
            "application_directory": str(self.application_root / "source"),
            "launcher": {
                "kind": "python",
                "executable": str(_environment_python(self.application_root / ".venv")),
            },
            "space_id": self.space_id,
            "commit": self.commit,
        }


@dataclass(frozen=True)
class ApplicationInstallResult:
    application_id: str
    name: str
    version: str
    application_root: Path
    source_url: str
    commit: str
    runtime_id: str
    replaced_existing: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.application_id,
            "name": self.name,
            "version": self.version,
            "application_root": str(self.application_root),
            "source_url": self.source_url,
            "commit": self.commit,
            "runtime_id": self.runtime_id,
            "replaced_existing": self.replaced_existing,
        }


@dataclass(frozen=True)
class ApplicationUninstallResult:
    application_id: str
    trash_root: Path

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.application_id,
            "trash_root": str(self.trash_root),
        }


@dataclass(frozen=True)
class _RuntimeResources:
    root: Path
    runtime_id: str
    python_executable: Path
    uv_executable: Path
    watcherobot_wheel: Path
    watcherobot_version: str
    watcherobot_sdk_commit: str


@dataclass(frozen=True)
class _StorePaths:
    root: Path

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def apps(self) -> Path:
        return self.root / "apps"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def trash(self) -> Path:
        return self.root / "trash"

    def app(self, application_id: str) -> Path:
        _require_application_id(application_id)
        return self.apps / application_id


def install_application(
    *,
    space_id: str,
    commit: str,
    store_root: Path,
    runtime_root: Path,
    hub: MarketplaceHubClient,
    events: EventSink | None = None,
    environment_runner: ApplicationEnvironmentRunner | None = None,
) -> ApplicationInstallResult:
    """Install one immutable remote Application into a single managed root."""

    paths = _open_store(store_root)
    _emit(events, "preparing_runtime", "Preparing locked Application Runtime")
    runtime = _prepare_runtime(paths, runtime_root)
    transaction_id = uuid.uuid4().hex
    transaction = paths.staging / transaction_id
    candidate = transaction / "candidate"
    source = candidate / "source"
    source.mkdir(parents=True, exist_ok=False)
    try:
        snapshot = download_application_snapshot(
            space_id=space_id,
            commit=commit,
            target=source,
            hub=hub,
            events=events,
            watcherobot_version=runtime.watcherobot_version,
        )
        application_id = snapshot.application.app_id
        _require_application_id(application_id)
        environment = candidate / ".venv"
        runner = environment_runner or SystemApplicationEnvironmentRunner()
        resolved_dependencies = _create_environment(
            candidate=candidate,
            environment=environment,
            runtime=runtime,
            dependencies=snapshot.application.dependencies,
            runner=runner,
            events=events,
        )
        _write_install_record(
            candidate=candidate,
            snapshot=snapshot,
            runtime=runtime,
            resolved_dependencies=resolved_dependencies,
        )
        _emit(events, "promoting_installation", "Publishing installed Application")
        destination = paths.app(application_id)
        replaced_existing = _promote_candidate(paths, transaction_id, candidate, destination)
        _remove_empty_directory(transaction)
        return ApplicationInstallResult(
            application_id=application_id,
            name=snapshot.application.name,
            version=snapshot.application.version,
            application_root=destination,
            source_url=snapshot.source_url,
            commit=snapshot.commit,
            runtime_id=runtime.runtime_id,
            replaced_existing=replaced_existing,
        )
    except DownloadError as exc:
        _move_transaction_to_trash(paths, transaction_id, transaction)
        raise ApplicationInstallError(exc.code, str(exc)) from exc
    except ApplicationInstallError:
        _move_transaction_to_trash(paths, transaction_id, transaction)
        raise
    except OSError as exc:
        _move_transaction_to_trash(paths, transaction_id, transaction)
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Application installation failed",
        ) from exc


def list_installed_applications(store_root: Path) -> tuple[InstalledApplication, ...]:
    """Return safe inventory records without starting or contacting the Daemon."""

    paths = _open_store(store_root)
    if not paths.apps.is_dir():
        return ()
    applications: list[InstalledApplication] = []
    for entry in sorted(paths.apps.iterdir(), key=lambda path: path.name):
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            applications.append(_read_installed_application(entry))
        except ApplicationInstallError:
            applications.append(
                InstalledApplication(
                    application_id=entry.name,
                    name=entry.name,
                    version="",
                    status="broken",
                    application_root=entry,
                )
            )
    return tuple(applications)


def uninstall_application(
    *,
    store_root: Path,
    application_id: str,
) -> ApplicationUninstallResult:
    """Move one managed Application root to recoverable local trash."""

    paths = _open_store(store_root)
    destination = paths.app(application_id)
    if destination.is_symlink() or not destination.is_dir():
        raise ApplicationInstallError(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            "Application is not installed",
        )
    trash_root = paths.trash / f"{uuid.uuid4().hex}-uninstall"
    try:
        os.replace(destination, trash_root)
    except OSError as exc:
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Unable to remove installed Application",
        ) from exc
    return ApplicationUninstallResult(application_id=application_id, trash_root=trash_root)


def _open_store(store_root: Path) -> _StorePaths:
    root = Path(store_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise OSError("store root is not a regular directory")
        root = root.resolve()
        paths = _StorePaths(root)
        for directory in (paths.apps, paths.staging, paths.trash):
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise OSError("store directory is not a regular directory")
        return paths
    except OSError as exc:
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Application Store is unavailable",
        ) from exc


def _prepare_runtime(paths: _StorePaths, source_root: Path) -> _RuntimeResources:
    source = _load_runtime(source_root)
    if paths.runtime.exists():
        current = _load_runtime(paths.runtime)
        if current.runtime_id != source.runtime_id:
            raise ApplicationInstallError(
                ErrorCode.INTERNAL_ERROR,
                "Installed Application Runtime does not match the requested Runtime",
            )
        return current
    staging = paths.staging / f"{uuid.uuid4().hex}-runtime"
    try:
        shutil.copytree(source.root, staging, copy_function=shutil.copy2)
        copied = _load_runtime(staging)
        if copied.runtime_id != source.runtime_id:
            raise ApplicationInstallError(
                ErrorCode.INTERNAL_ERROR,
                "Copied Application Runtime does not match the requested Runtime",
            )
        os.replace(staging, paths.runtime)
        return _load_runtime(paths.runtime)
    except ApplicationInstallError:
        _remove_tree(staging)
        raise
    except OSError as exc:
        _remove_tree(staging)
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Unable to prepare Application Runtime",
        ) from exc


def _load_runtime(runtime_root: Path) -> _RuntimeResources:
    try:
        root = _regular_directory(runtime_root, "Application Runtime")
        manifest_path = _regular_file(
            root / _RUNTIME_MANIFEST,
            "Application Runtime manifest",
        )
        if manifest_path.stat().st_size > _MAX_OUTPUT_BYTES:
            raise ValueError("manifest is too large")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ApplicationInstallError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, ApplicationInstallError):
            raise
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Application Runtime is invalid",
        ) from exc
    try:
        _require_runtime_identity(manifest)
        python_root = _manifest_directory(root, manifest["python"]["root"], "Python Runtime")
        python = _manifest_file(root, manifest["python"]["executable"], "Python executable")
        if not python.is_relative_to(python_root):
            raise ValueError("Python executable escapes its Runtime root")
        if _runtime_tree_sha256(python_root) != manifest["python"]["tree_sha256"]:
            raise ValueError("Python Runtime hash mismatch")
        uv = _manifest_file(root, manifest["uv"]["executable"], "uv executable")
        wheel = _manifest_file(root, manifest["watcherobot"]["wheel"], "watcherobot wheel")
        if _sha256(uv) != manifest["uv"]["sha256"]:
            raise ValueError("uv hash mismatch")
        if _sha256(wheel) != manifest["watcherobot"]["sha256"]:
            raise ValueError("watcherobot wheel hash mismatch")
        if not wheel.name.startswith("watcherobot-") or wheel.suffix != ".whl":
            raise ValueError("watcherobot wheel is invalid")
        return _RuntimeResources(
            root=root,
            runtime_id=manifest["runtime_id"],
            python_executable=python,
            uv_executable=uv,
            watcherobot_wheel=wheel,
            watcherobot_version=manifest["watcherobot"]["version"],
            watcherobot_sdk_commit=manifest["watcherobot"]["sdk_commit"],
        )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Application Runtime is invalid",
        ) from exc


def _require_runtime_identity(manifest: object) -> None:
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "runtime_id",
        "platform",
        "python",
        "uv",
        "watcherobot",
    }:
        raise ValueError("runtime manifest schema is invalid")
    if manifest["schema_version"] != _RUNTIME_SCHEMA_VERSION:
        raise ValueError("runtime manifest version is unsupported")
    if manifest["platform"] != _platform_resource_name():
        raise ValueError("runtime platform does not match host")
    python = manifest["python"]
    uv = manifest["uv"]
    watcherobot = manifest["watcherobot"]
    if not all(isinstance(value, dict) for value in (python, uv, watcherobot)):
        raise ValueError("runtime resources are invalid")
    if python.get("implementation") != "cpython":
        raise ValueError("runtime Python implementation is invalid")
    for value in (
        python.get("version"),
        uv.get("version"),
        watcherobot.get("version"),
    ):
        if not _safe_version(value):
            raise ValueError("runtime version is invalid")
    for value in (
        python.get("source_sha256"),
        python.get("tree_sha256"),
        uv.get("source_sha256"),
        uv.get("sha256"),
        watcherobot.get("sha256"),
    ):
        if not _lower_hex(value, 64):
            raise ValueError("runtime hash is invalid")
    if not _lower_hex(watcherobot.get("sdk_commit"), 40):
        raise ValueError("runtime SDK commit is invalid")
    expected_id = (
        f"{manifest['platform']}-python-{python['version']}-"
        f"watcherobot-{watcherobot['version']}"
    )
    if manifest["runtime_id"] != expected_id:
        raise ValueError("runtime ID does not match locked versions")


def _create_environment(
    *,
    candidate: Path,
    environment: Path,
    runtime: _RuntimeResources,
    dependencies: tuple[str, ...],
    runner: ApplicationEnvironmentRunner,
    events: EventSink | None,
) -> tuple[dict[str, str], ...]:
    python = _environment_python(environment)
    command_environment = (
        ("WATCHER_EXPECTED_SDK_VERSION", runtime.watcherobot_version),
    )
    commands = (
        ApplicationEnvironmentCommand(
            stage="creating_environment",
            executable=runtime.uv_executable,
            arguments=(
                "--no-config",
                "--no-python-downloads",
                "venv",
                "--python",
                str(runtime.python_executable),
                str(environment),
            ),
            current_dir=candidate,
            environment_root=environment,
            environment=command_environment,
        ),
        ApplicationEnvironmentCommand(
            stage="installing_dependencies",
            executable=runtime.uv_executable,
            arguments=(
                "--no-config",
                "--no-python-downloads",
                "pip",
                "install",
                "--python",
                str(python),
                str(runtime.watcherobot_wheel),
                *dependencies,
            ),
            current_dir=candidate,
            environment_root=environment,
            environment=command_environment,
        ),
        ApplicationEnvironmentCommand(
            stage="checking_dependencies",
            executable=runtime.uv_executable,
            arguments=(
                "--no-config",
                "--no-python-downloads",
                "pip",
                "check",
                "--python",
                str(python),
            ),
            current_dir=candidate,
            environment_root=environment,
            environment=command_environment,
        ),
        ApplicationEnvironmentCommand(
            stage="compiling_entrypoint",
            executable=python,
            arguments=("-m", "py_compile", str(candidate / "source/app.py")),
            current_dir=candidate,
            environment_root=environment,
            environment=command_environment,
        ),
        ApplicationEnvironmentCommand(
            stage="importing_watcherobot",
            executable=python,
            arguments=(
                "-c",
                "import os,sys,watcherobot;sys.exit(0 if watcherobot.__version__ == os.environ['WATCHER_EXPECTED_SDK_VERSION'] else 1)",
            ),
            current_dir=candidate,
            environment_root=environment,
            environment=command_environment,
        ),
        ApplicationEnvironmentCommand(
            stage="listing_dependencies",
            executable=runtime.uv_executable,
            arguments=(
                "--no-config",
                "--no-python-downloads",
                "pip",
                "list",
                "--python",
                str(python),
                "--format",
                "json",
            ),
            current_dir=candidate,
            environment_root=environment,
            environment=command_environment,
        ),
        ApplicationEnvironmentCommand(
            stage="freezing_dependencies",
            executable=runtime.uv_executable,
            arguments=(
                "--no-config",
                "--no-python-downloads",
                "pip",
                "freeze",
                "--python",
                str(python),
            ),
            current_dir=candidate,
            environment_root=environment,
            environment=command_environment,
        ),
    )
    outputs: dict[str, ApplicationEnvironmentOutput] = {}
    for command in commands:
        _emit(events, command.stage, _stage_message(command.stage))
        try:
            output = runner.run(command)
        except ApplicationInstallError:
            raise
        except Exception as exc:
            raise ApplicationInstallError(
                ErrorCode.INTERNAL_ERROR,
                "Application environment command failed",
            ) from exc
        if len(output.stdout) > _MAX_OUTPUT_BYTES or len(output.stderr) > _MAX_OUTPUT_BYTES:
            raise ApplicationInstallError(
                ErrorCode.INTERNAL_ERROR,
                "Application environment command failed",
            )
        outputs[command.stage] = output
    return _resolved_dependencies(
        outputs["listing_dependencies"].stdout,
        runtime.watcherobot_version,
    )


def _write_install_record(
    *,
    candidate: Path,
    snapshot: object,
    runtime: _RuntimeResources,
    resolved_dependencies: tuple[dict[str, str], ...],
) -> None:
    application = snapshot.application
    record = {
        "schema_version": 1,
        "id": application.app_id,
        "name": application.name,
        "version": application.version,
        "installed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": {
            "space_id": snapshot.space_id,
            "commit": snapshot.commit,
            "source_url": snapshot.source_url,
        },
        "runtime": {
            "runtime_id": runtime.runtime_id,
            "watcherobot_version": runtime.watcherobot_version,
            "sdk_commit": runtime.watcherobot_sdk_commit,
        },
        "launcher": {
            "kind": "python",
            "executable": _environment_python(Path(".venv")).as_posix(),
            "arguments": ["source/app.py"],
        },
        "dependencies": list(resolved_dependencies),
    }
    try:
        candidate.joinpath("install.json").write_text(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Unable to write Application install record",
        ) from exc


def _promote_candidate(
    paths: _StorePaths,
    transaction_id: str,
    candidate: Path,
    destination: Path,
) -> bool:
    replaced = destination.exists()
    previous = paths.trash / f"{transaction_id}-previous"
    try:
        if replaced:
            if destination.is_symlink() or not destination.is_dir():
                raise OSError("existing Application root is invalid")
            os.replace(destination, previous)
        os.replace(candidate, destination)
        return replaced
    except OSError as exc:
        if replaced and previous.exists() and not destination.exists():
            try:
                os.replace(previous, destination)
            except OSError:
                pass
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Unable to publish installed Application",
        ) from exc


def _read_installed_application(root: Path) -> InstalledApplication:
    try:
        record = json.loads(root.joinpath("install.json").read_text(encoding="utf-8"))
        application_id = record["id"]
        source = record["source"]
        if (
            not isinstance(application_id, str)
            or application_id != root.name
            or not _APPLICATION_ID.fullmatch(application_id)
            or not root.joinpath("source/app.json").is_file()
            or not root.joinpath("source/app.py").is_file()
            or not _environment_python(root / ".venv").is_file()
        ):
            raise ValueError("invalid Application record")
        return InstalledApplication(
            application_id=application_id,
            name=_required_text(record["name"]),
            version=_required_text(record["version"]),
            status="installed",
            application_root=root,
            space_id=_required_text(source["space_id"]),
            commit=_required_text(source["commit"]),
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApplicationInstallError(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            "Installed Application record is invalid",
        ) from exc


def _resolved_dependencies(payload: bytes, expected_watcherobot_version: str) -> tuple[dict[str, str], ...]:
    try:
        listed = json.loads(payload)
        if not isinstance(listed, list):
            raise ValueError("package list is invalid")
        resolved = tuple(
            {
                "name": _required_text(item["name"]).lower(),
                "version": _required_text(item["version"]),
            }
            for item in listed
            if isinstance(item, dict)
        )
        versions = {item["name"]: item["version"] for item in resolved}
        if len(resolved) != len(listed) or versions.get("watcherobot") != expected_watcherobot_version:
            raise ValueError("watcherobot version is invalid")
        return tuple(sorted(resolved, key=lambda item: item["name"]))
    except (UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Application environment dependency report is invalid",
        ) from exc


def _manifest_file(root: Path, value: object, label: str) -> Path:
    path = _manifest_path(root, value, label)
    return _regular_file(path, label)


def _manifest_directory(root: Path, value: object, label: str) -> Path:
    path = _manifest_path(root, value, label)
    return _regular_directory(path, label)


def _manifest_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} path is invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"{label} path is invalid")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} path contains a symbolic link")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{label} path escapes Runtime")
    return resolved


def _regular_directory(path: Path, label: str) -> Path:
    try:
        if path.is_symlink() or not path.is_dir():
            raise OSError(f"{label} is not a regular directory")
        return path.resolve()
    except OSError as exc:
        raise ApplicationInstallError(
            ErrorCode.INTERNAL_ERROR,
            "Application Runtime is invalid",
        ) from exc


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is not a regular file")
    return path.resolve()


def _runtime_tree_sha256(root: Path) -> str:
    digest = hashlib.sha256(_RUNTIME_TREE_PREFIX)
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Runtime contains a symbolic link")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ValueError("Runtime contains an unsupported entry")
    for path in sorted(files, key=lambda value: value.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "little"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "little"))
        with path.open("rb") as file:
            while block := file.read(64 * 1024):
                digest.update(block)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(64 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts/python.exe"
    return environment / "bin/python"


def _move_transaction_to_trash(
    paths: _StorePaths,
    transaction_id: str,
    transaction: Path,
) -> None:
    if not transaction.exists():
        return
    try:
        os.replace(transaction, paths.trash / transaction_id)
    except OSError:
        _remove_tree(transaction)


def _remove_empty_directory(directory: Path) -> None:
    try:
        directory.rmdir()
    except OSError:
        pass


def _remove_tree(directory: Path) -> None:
    if directory.exists() and not directory.is_symlink():
        shutil.rmtree(directory, ignore_errors=True)


def _require_application_id(application_id: str) -> None:
    if _APPLICATION_ID.fullmatch(application_id) is None:
        raise ApplicationInstallError(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            "Application ID is invalid",
        )


def _safe_version(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 64
        and all(character.isascii() and (character.isalnum() or character in ".-+_") for character in value)
    )


def _lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _required_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("required record field is invalid")
    return value


def _platform_resource_name() -> str:
    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "win32-x64"
    if sys.platform.startswith("linux") and machine in {"amd64", "x86_64"}:
        return "linux-x64"
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "darwin-arm64"
    raise ValueError("host platform has no supported Application Runtime")


def _stage_message(stage: str) -> str:
    return {
        "creating_environment": "Creating isolated Application environment",
        "installing_dependencies": "Installing Application dependencies",
        "checking_dependencies": "Checking Application dependencies",
        "compiling_entrypoint": "Validating Application entrypoint",
        "importing_watcherobot": "Checking Application SDK runtime",
        "listing_dependencies": "Recording installed dependencies",
        "freezing_dependencies": "Freezing installed dependencies",
    }[stage]


def _emit(events: EventSink | None, stage: str, message: str) -> None:
    if events is not None:
        events.emit(ProgressEvent(stage=stage, message=message))
