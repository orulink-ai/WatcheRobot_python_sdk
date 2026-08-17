"""Controlled launch specifications for one selected Application."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .manifest import ApplicationManifest


DEFAULT_APPLICATION_ID = "watcher_default"
INVALID_APPLICATION_LAUNCHER = "invalid_application_launcher"
_POSIX_PYTHON_NAME = re.compile(r"^python(?:3(?:\.\d+)?)?$")


class ApplicationLaunchError(ValueError):
    """Reject a launch request before it can become a process command."""

    code = INVALID_APPLICATION_LAUNCHER


class ApplicationLauncherKind(str, Enum):
    """The only launcher types accepted by the Daemon control plane."""

    PYTHON = "python"
    BUNDLED = "bundled"


@dataclass(frozen=True)
class ApplicationLaunchSpec:
    """A validated fixed-entrypoint process specification."""

    app_id: str
    application_dir: Path
    kind: ApplicationLauncherKind
    executable: Path
    command_executable: Path

    @property
    def entrypoint(self) -> Path:
        return self.application_dir / "app.py"

    @property
    def command(self) -> tuple[Path, ...]:
        if self.kind is ApplicationLauncherKind.BUNDLED:
            return (self.command_executable,)
        return (self.command_executable, self.entrypoint)


class ApplicationLauncher:
    """Validate launchers against roots fixed when the Daemon starts."""

    def __init__(
        self,
        *,
        managed_app_root: Path,
        bundled_resource_root: Path,
        source_default_application_root: Path | None = None,
        source_default_launcher_executable: Path | None = None,
        default_app_id: str = DEFAULT_APPLICATION_ID,
        is_windows: bool | None = None,
    ) -> None:
        if (source_default_application_root is None) != (
            source_default_launcher_executable is None
        ):
            raise ValueError(
                "source default Application root and launcher "
                "must be configured together"
            )
        self._managed_app_root = Path(managed_app_root).resolve()
        self._bundled_resource_root = Path(bundled_resource_root).resolve()
        self._source_default_application_root = (
            Path(source_default_application_root).resolve()
            if source_default_application_root is not None
            else None
        )
        self._source_default_launcher_executable = (
            Path(os.path.abspath(source_default_launcher_executable))
            if source_default_launcher_executable is not None
            else None
        )
        self._default_app_id = default_app_id
        self._is_windows = os.name == "nt" if is_windows is None else is_windows

    def build_spec(
        self,
        *,
        application_dir: Path,
        kind: str | ApplicationLauncherKind,
        executable: Path,
    ) -> ApplicationLaunchSpec:
        """Build a spec without accepting arguments or an entrypoint."""

        selected_dir = _require_absolute_directory(application_dir)
        manifest = ApplicationManifest.load(selected_dir)
        launcher_kind = _parse_kind(kind)
        _require_kind_matches_application(
            launcher_kind,
            app_id=manifest.app_id,
            default_app_id=self._default_app_id,
        )
        trusted_source_default = (
            launcher_kind is ApplicationLauncherKind.PYTHON
            and manifest.app_id == self._default_app_id
            and self._source_default_application_root is not None
        )
        requested_executable = Path(os.path.abspath(executable))
        if trusted_source_default:
            if selected_dir != self._source_default_application_root:
                raise ApplicationLaunchError(
                    "Source default Application root does not match its trusted root"
                )
            if requested_executable != self._source_default_launcher_executable:
                raise ApplicationLaunchError(
                    "Source default Application launcher does not match "
                    "its trusted launcher"
                )
            _validate_trusted_source_default_executable(
                requested_executable,
                is_windows=self._is_windows,
            )
            spec_executable = requested_executable
            command_executable = _python_executable_for_trusted_source_default(
                requested_executable,
                is_windows=self._is_windows,
            )
        else:
            controlled_root = (
                self._bundled_resource_root
                if launcher_kind is ApplicationLauncherKind.BUNDLED
                else self._managed_app_root
            )
            resolved_executable = _require_controlled_executable(
                requested_executable,
                controlled_root=controlled_root,
                kind=launcher_kind,
                is_windows=self._is_windows,
            )
            spec_executable = (
                requested_executable
                if launcher_kind is ApplicationLauncherKind.PYTHON
                else resolved_executable
            )
            command_executable = (
                _python_executable_for_application(
                    requested_executable,
                    controlled_root=controlled_root,
                    is_windows=self._is_windows,
                )
                if launcher_kind is ApplicationLauncherKind.PYTHON
                else resolved_executable
            )
        if (
            launcher_kind is ApplicationLauncherKind.BUNDLED
            and not selected_dir.is_relative_to(self._bundled_resource_root)
        ):
            raise ApplicationLaunchError(
                "Application directory must stay inside its controlled root"
            )
        return ApplicationLaunchSpec(
            app_id=manifest.app_id,
            application_dir=selected_dir,
            kind=launcher_kind,
            executable=spec_executable,
            command_executable=command_executable,
        )


def _validate_trusted_source_default_executable(
    executable: Path,
    *,
    is_windows: bool,
) -> None:
    """Validate the explicitly authorized launcher while retaining its venv path."""

    resolved = _require_executable_file(executable, is_windows=is_windows)
    _require_platform_executable_name(
        resolved,
        kind=ApplicationLauncherKind.PYTHON,
        is_windows=is_windows,
    )
    if is_windows:
        trusted_launcher_directory = executable.parent.resolve(strict=True)
        if resolved.parent != trusted_launcher_directory:
            raise ApplicationLaunchError(
                "Source default Application launcher must stay inside "
                "its trusted directory"
            )


def _require_absolute_directory(application_dir: Path) -> Path:
    requested = Path(application_dir)
    if not requested.is_absolute():
        raise ApplicationLaunchError(
            "Application directory must be an absolute path"
        )
    try:
        resolved = requested.resolve(strict=True)
    except OSError as exc:
        raise ApplicationLaunchError(
            "Application directory does not exist"
        ) from exc
    if not resolved.is_dir():
        raise ApplicationLaunchError(
            "Application directory must be a directory"
        )
    return resolved


def _parse_kind(kind: str | ApplicationLauncherKind) -> ApplicationLauncherKind:
    try:
        return ApplicationLauncherKind(kind)
    except ValueError as exc:
        raise ApplicationLaunchError(
            "Application launcher kind is not supported"
        ) from exc


def _require_kind_matches_application(
    kind: ApplicationLauncherKind,
    *,
    app_id: str,
    default_app_id: str,
) -> None:
    if kind is ApplicationLauncherKind.BUNDLED and app_id != default_app_id:
        raise ApplicationLaunchError(
            "Bundled launcher is reserved for the default Application"
        )


def _require_controlled_executable(
    executable: Path,
    *,
    controlled_root: Path,
    kind: ApplicationLauncherKind,
    is_windows: bool,
) -> Path:
    requested = Path(executable)
    if not requested.is_absolute():
        raise ApplicationLaunchError(
            "Application launcher executable must be an absolute path"
        )
    try:
        normalized_requested = (
            requested.parent.resolve(strict=True) / requested.name
        )
    except OSError as exc:
        raise ApplicationLaunchError(
            "Application launcher executable does not exist"
        ) from exc
    if not normalized_requested.is_relative_to(controlled_root):
        raise ApplicationLaunchError(
            "Application launcher executable path must stay inside "
            "its controlled root"
        )
    resolved = _require_executable_file(requested, is_windows=is_windows)
    if (
        (kind is ApplicationLauncherKind.BUNDLED or is_windows)
        and not resolved.is_relative_to(controlled_root)
    ):
        raise ApplicationLaunchError(
            "Application launcher executable must stay inside its controlled root"
        )
    _require_platform_executable_name(
        resolved,
        kind=kind,
        is_windows=is_windows,
    )
    return resolved


def _require_executable_file(executable: Path, *, is_windows: bool) -> Path:
    try:
        resolved = executable.resolve(strict=True)
    except OSError as exc:
        raise ApplicationLaunchError(
            "Application launcher executable does not exist"
        ) from exc
    if not resolved.is_file() or (
        not is_windows and not os.access(resolved, os.X_OK)
    ):
        raise ApplicationLaunchError(
            "Application launcher executable is not executable"
        )
    return resolved


def _require_platform_executable_name(
    executable: Path,
    *,
    kind: ApplicationLauncherKind,
    is_windows: bool,
) -> None:
    name = executable.name.lower()
    if kind is ApplicationLauncherKind.PYTHON:
        valid = (
            name == "python.exe"
            if is_windows
            else _POSIX_PYTHON_NAME.fullmatch(name) is not None
        )
    else:
        expected = (
            "watcher-default-app.exe"
            if is_windows
            else "watcher-default-app"
        )
        valid = name == expected
    if not valid:
        raise ApplicationLaunchError(
            "Application launcher executable name is not allowed"
        )


def _python_executable_for_application(
    executable: Path,
    *,
    controlled_root: Path,
    is_windows: bool,
) -> Path:
    """Use the windowless interpreter for managed Python Applications on Windows.

    A Windows virtual environment's ``python.exe`` is a redirector.  It starts
    the base interpreter in a second process, which can allocate a new console
    even when the Daemon hid the first process.  ``pythonw.exe`` is the matching
    redirector without that console allocation.  The executable remains fixed
    to the Application's controlled environment and stdout/stderr are still
    captured by the Daemon when available.
    """

    if not is_windows:
        return executable
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.is_file():
        return executable
    try:
        resolved = pythonw.resolve(strict=True)
    except OSError as exc:
        raise ApplicationLaunchError(
            "Application launcher executable does not exist"
        ) from exc
    if not resolved.is_file():
        raise ApplicationLaunchError(
            "Application launcher executable is not executable"
        )
    if not resolved.is_relative_to(controlled_root):
        raise ApplicationLaunchError(
            "Application launcher executable must stay inside its controlled root"
        )
    if resolved.name.lower() != "pythonw.exe":
        raise ApplicationLaunchError(
            "Application launcher executable name is not allowed"
        )
    return resolved


def _python_executable_for_trusted_source_default(
    executable: Path,
    *,
    is_windows: bool,
) -> Path:
    """Retain trusted POSIX venv semantics and prefer adjacent Windows pythonw.

    On POSIX, executing the original venv path is required for Python to find
    ``pyvenv.cfg`` and load Workspace-only dependencies.  This exception is
    limited to the exact launcher path supplied by the trusted Desktop / SDK
    source-runtime orchestration; its parent directory must not be writable by
    an installed third-party Application. Ordinary POSIX Applications preserve
    the same venv path semantics only when that path is inside their managed
    root; Windows launchers and bundled executables retain stricter resolved-
    target containment.

    A complete Windows venv normally includes ``pythonw.exe``. Falling back to
    the already validated adjacent ``python.exe`` keeps source development
    usable in minimal managed environments without expanding the trusted
    launcher directory.
    """

    if not is_windows:
        return executable
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.is_file():
        return executable
    resolved = _require_executable_file(pythonw, is_windows=True)
    trusted_launcher_directory = executable.parent.resolve(strict=True)
    if (
        pythonw.parent != executable.parent
        or not resolved.is_relative_to(trusted_launcher_directory)
        or resolved.name.lower() != "pythonw.exe"
    ):
        raise ApplicationLaunchError(
            "Source default Application pythonw launcher is not allowed"
        )
    return resolved
