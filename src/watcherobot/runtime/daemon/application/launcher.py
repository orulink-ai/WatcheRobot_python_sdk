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
    is_windows: bool

    @property
    def entrypoint(self) -> Path:
        return self.application_dir / "app.py"

    @property
    def command(self) -> tuple[Path, ...]:
        if self.kind is ApplicationLauncherKind.BUNDLED:
            return (self.executable,)
        return (
            _python_executable_for_application(
                self.executable,
                is_windows=self.is_windows,
            ),
            self.entrypoint,
        )


class ApplicationLauncher:
    """Validate launchers against roots fixed when the Daemon starts."""

    def __init__(
        self,
        *,
        managed_app_root: Path,
        bundled_resource_root: Path,
        default_app_id: str = DEFAULT_APPLICATION_ID,
        is_windows: bool | None = None,
    ) -> None:
        self._managed_app_root = Path(managed_app_root).resolve()
        self._bundled_resource_root = Path(bundled_resource_root).resolve()
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
        controlled_root = (
            self._bundled_resource_root
            if launcher_kind is ApplicationLauncherKind.BUNDLED
            else self._managed_app_root
        )
        resolved_executable = _require_controlled_executable(
            executable,
            controlled_root=controlled_root,
            kind=launcher_kind,
            is_windows=self._is_windows,
        )
        if (
            launcher_kind is ApplicationLauncherKind.BUNDLED
            and not selected_dir.is_relative_to(controlled_root)
        ):
            raise ApplicationLaunchError(
                "Application directory must stay inside its controlled root"
            )
        return ApplicationLaunchSpec(
            app_id=manifest.app_id,
            application_dir=selected_dir,
            kind=launcher_kind,
            executable=resolved_executable,
            is_windows=self._is_windows,
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
    if kind is ApplicationLauncherKind.PYTHON and app_id == default_app_id:
        raise ApplicationLaunchError(
            "Default Application must use the bundled launcher"
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
        resolved = requested.resolve(strict=True)
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
    if not resolved.is_relative_to(controlled_root):
        raise ApplicationLaunchError(
            "Application launcher executable must stay inside its controlled root"
        )
    _require_platform_executable_name(
        resolved,
        kind=kind,
        is_windows=is_windows,
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
    if pythonw.is_file():
        return pythonw.resolve()
    return executable
