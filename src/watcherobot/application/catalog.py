"""Safe .wapp packaging and the per-user Application catalog."""

from __future__ import annotations

import json
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifest,
    ApplicationManifestError,
)

MAX_PACKAGE_FILES = 1000
MAX_PACKAGE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
_PACKAGE_IGNORE_FILE = ".wappignore"
PROTECTED_APPLICATION_IDS = frozenset({"watcher_default"})


class ApplicationCatalogError(RuntimeError):
    """Base error for .wapp and Catalog operations."""


class CatalogPackageError(ApplicationCatalogError):
    """Raised when a .wapp archive is unsafe or malformed."""


class CatalogConflictError(ApplicationCatalogError):
    """Raised when the same Application id and version already exists."""


class CatalogBusyError(ApplicationCatalogError):
    """Raised when a running Application makes Catalog mutation unsafe."""


class CatalogNotFoundError(ApplicationCatalogError):
    """Raised when a requested Catalog entry doesn't exist."""


class ProtectedApplicationError(ApplicationCatalogError):
    """Raised when uninstalling a built-in Application is attempted."""


@dataclass(frozen=True)
class CatalogEntry:
    app_id: str
    name: str
    version: str
    path: Path
    requires_watcherobot: str


class ApplicationCatalog:
    """Install and select immutable Application versions."""

    def __init__(
        self,
        root: Path,
        *,
        is_runtime_active: Callable[[], bool] = lambda: False,
    ) -> None:
        self.root = Path(root).resolve()
        self.apps_root = self.root / "applications"
        self.selection_path = self.root / "selected.json"
        self._is_runtime_active = is_runtime_active

    def install(self, archive: Path) -> CatalogEntry:
        self._require_mutable()
        archive_path = Path(archive).resolve()
        if archive_path.suffix.lower() != ".wapp":
            raise CatalogPackageError("Application package must use .wapp")
        if not archive_path.is_file():
            raise CatalogPackageError(
                f"Application package does not exist: {archive_path}"
            )

        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".install-",
            dir=self.root,
        ) as temporary_directory:
            extracted_root = Path(temporary_directory) / "application"
            extracted_root.mkdir()
            _extract_package(archive_path, extracted_root)
            try:
                manifest = ApplicationManifest.load(extracted_root)
            except ApplicationManifestError as exc:
                raise CatalogPackageError(str(exc)) from exc

            destination = (
                self.apps_root / manifest.app_id / manifest.version
            ).resolve()
            _require_inside(destination, self.apps_root.resolve())
            if destination.exists():
                raise CatalogConflictError(
                    f"{manifest.app_id}@{manifest.version} is already installed"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            extracted_root.replace(destination)

        return _entry_from_manifest(manifest, destination)

    def list(self) -> list[CatalogEntry]:
        if not self.apps_root.is_dir():
            return []
        entries: list[CatalogEntry] = []
        for manifest_path in sorted(self.apps_root.glob("*/*/app.json")):
            application_dir = manifest_path.parent.resolve()
            manifest = ApplicationManifest.load(application_dir)
            entries.append(_entry_from_manifest(manifest, application_dir))
        return entries

    def select(
        self,
        app_id: str,
        *,
        version: str | None = None,
    ) -> CatalogEntry:
        self._require_mutable()
        entry = self._find(app_id, version=version)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.selection_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"id": entry.app_id, "version": entry.version},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.selection_path)
        return entry

    def selected(self) -> CatalogEntry | None:
        try:
            payload = json.loads(
                self.selection_path.read_text(encoding="utf-8")
            )
            return self._find(
                str(payload["id"]),
                version=str(payload["version"]),
            )
        except (
            OSError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            CatalogNotFoundError,
        ):
            return None

    def uninstall(
        self,
        app_id: str,
        *,
        version: str | None = None,
    ) -> None:
        self._require_mutable()
        if app_id in PROTECTED_APPLICATION_IDS:
            raise ProtectedApplicationError(
                f"{app_id} is a protected built-in Application"
            )
        entry = self._find(app_id, version=version)
        selected = self.selected()
        if selected == entry:
            try:
                self.selection_path.unlink()
            except FileNotFoundError:
                pass

        applications_root = self.apps_root.resolve()
        target = entry.path.resolve()
        _require_inside(target, applications_root)
        shutil.rmtree(target)
        app_root = target.parent
        try:
            app_root.rmdir()
        except OSError:
            pass

    def _find(
        self,
        app_id: str,
        *,
        version: str | None,
    ) -> CatalogEntry:
        candidates = [
            entry
            for entry in self.list()
            if entry.app_id == app_id
            and (version is None or entry.version == version)
        ]
        if not candidates:
            suffix = f"@{version}" if version else ""
            raise CatalogNotFoundError(f"{app_id}{suffix} is not installed")
        if version is None and len(candidates) > 1:
            raise CatalogConflictError(
                f"{app_id} has multiple installed versions; specify one"
            )
        return candidates[0]

    def _require_mutable(self) -> None:
        if self._is_runtime_active():
            raise CatalogBusyError(
                "Catalog cannot change while an Application is running"
            )


def package_application(
    application_dir: Path,
    output_path: Path,
) -> Path:
    source = Path(application_dir).resolve()
    output = Path(output_path).resolve()
    if output.suffix.lower() != ".wapp":
        raise CatalogPackageError("Application package must use .wapp")
    try:
        ApplicationManifest.load(source)
    except ApplicationManifestError as exc:
        raise CatalogPackageError(str(exc)) from exc

    ignore_patterns = _load_package_ignore_patterns(source)
    files = []
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.resolve() == output:
            continue
        relative = path.relative_to(source)
        if relative.as_posix() == _PACKAGE_IGNORE_FILE:
            continue
        if any(
            part in {".git", "__pycache__", ".pytest_cache"}
            for part in relative.parts
        ):
            continue
        if _is_package_path_ignored(relative, ignore_patterns):
            continue
        files.append(path)
    if len(files) > MAX_PACKAGE_FILES:
        raise CatalogPackageError("Application package contains too many files")
    total_size = sum(path.stat().st_size for path in files)
    if total_size > MAX_PACKAGE_UNCOMPRESSED_BYTES:
        raise CatalogPackageError("Application package is too large")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".wapp.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as package:
            for path in files:
                package.write(
                    path,
                    path.relative_to(source).as_posix(),
                )
        temporary.replace(output)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return output


def _load_package_ignore_patterns(source: Path) -> tuple[str, ...]:
    ignore_path = source / _PACKAGE_IGNORE_FILE
    if not ignore_path.is_file():
        return ()
    try:
        lines = ignore_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CatalogPackageError(
            f"Cannot read {_PACKAGE_IGNORE_FILE}: {exc}"
        ) from exc
    return tuple(
        line.strip().replace("\\", "/")
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )


def _is_package_path_ignored(
    relative_path: Path,
    patterns: tuple[str, ...],
) -> bool:
    relative = PurePosixPath(relative_path.as_posix())
    text = relative.as_posix()
    for pattern in patterns:
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            ancestors = (
                "/".join(relative.parts[:depth])
                for depth in range(1, len(relative.parts))
            )
            if any(fnmatchcase(ancestor, prefix) for ancestor in ancestors):
                return True
            continue
        if relative.match(pattern):
            return True
    return False


def _extract_package(archive: Path, destination: Path) -> None:
    try:
        package = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise CatalogPackageError("Application package is not a valid ZIP") from exc

    with package:
        members = package.infolist()
        if len(members) > MAX_PACKAGE_FILES:
            raise CatalogPackageError("Application package contains too many files")
        total_size = sum(member.file_size for member in members)
        if total_size > MAX_PACKAGE_UNCOMPRESSED_BYTES:
            raise CatalogPackageError("Application package is too large")

        seen: set[str] = set()
        for member in members:
            member_path = PurePosixPath(member.filename)
            normalized_name = member_path.as_posix()
            if (
                member_path.is_absolute()
                or ".." in member_path.parts
                or not member_path.parts
                or member_path.parts[0] in {"", "."}
            ):
                raise CatalogPackageError(
                    f"Application package contains unsafe path: {member.filename}"
                )
            if normalized_name in seen:
                raise CatalogPackageError(
                    f"Application package contains duplicate path: {normalized_name}"
                )
            seen.add(normalized_name)
            file_type = (member.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise CatalogPackageError(
                    f"Application package contains unsafe symlink: {normalized_name}"
                )

            target = destination.joinpath(*member_path.parts).resolve()
            _require_inside(target, destination.resolve())
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _entry_from_manifest(
    manifest: ApplicationManifest,
    path: Path,
) -> CatalogEntry:
    return CatalogEntry(
        app_id=manifest.app_id,
        name=manifest.name,
        version=manifest.version,
        path=path.resolve(),
        requires_watcherobot=manifest.requires_watcherobot,
    )


def _require_inside(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CatalogPackageError(f"unsafe path escapes Catalog: {path}") from exc
