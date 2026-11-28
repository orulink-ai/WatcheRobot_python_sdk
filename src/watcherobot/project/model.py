"""Local development project discovery; no runtime or device I/O."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from watcherobot.distribution.marketplace_icons import image_data_url
from watcherobot.firmware.contract import MAX_FIRMWARE_ICON_BYTES, firmware_id, version
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifest,
    ApplicationManifestError,
)

PROJECT_MANIFEST = "watcherobot.project.json"
FIRMWARE_MANIFEST = "firmware.project.json"
KINDS = ("application", "firmware", "combined")


class ProjectError(ValueError):
    """Invalid or unsafe development project."""


def reject_symlinks(path: Path) -> None:
    """Reject symlinks/junctions before resolution, including dangling links."""
    for part in (path, *path.parents):
        try:
            attributes = getattr(part.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        if part.is_symlink() or attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ProjectError(
                f"Symbolic links and junctions are not supported: {part}"
            )


def safe_relative(root: Path, value: object, *, allow_root: bool = False) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProjectError("Member path must be a nonempty relative POSIX path")
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if posix.is_absolute() or windows.drive or windows.root or ".." in posix.parts:
        raise ProjectError(f"Unsafe relative path: {value}")
    target = root / value
    reject_symlinks(target)
    if target.resolve() == root and not allow_root:
        raise ProjectError("Member path must identify a child directory")
    if not target.resolve().is_relative_to(root):
        raise ProjectError(f"Path escapes project root: {value}")
    return target.resolve()


def read_document(path: Path) -> dict[str, Any]:
    reject_symlinks(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProjectError(f"Cannot read project metadata: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectError(f"Project metadata must be an object: {path}")
    return value


@dataclass(frozen=True)
class DevelopmentProject:
    kind: str
    project_root: Path
    application_directory: Path | None
    firmware_directory: Path | None
    firmware_metadata: dict[str, str] | None
    firmware_build_directory: Path | None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "project_root": str(self.project_root),
            "application_directory": str(self.application_directory)
            if self.application_directory
            else None,
            "firmware_directory": str(self.firmware_directory)
            if self.firmware_directory
            else None,
            "firmware_metadata": self.firmware_metadata,
            "firmware_build_directory": str(self.firmware_build_directory)
            if self.firmware_build_directory
            else None,
        }


def load_project(directory: Path) -> DevelopmentProject:
    reject_symlinks(Path(directory).absolute())
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ProjectError(f"Project directory does not exist: {root}")
    manifest = root / PROJECT_MANIFEST
    reject_symlinks(manifest)
    if manifest.exists():
        document = read_document(manifest)
        if (
            type(document.get("schema_version")) is not int
            or document["schema_version"] != 1
        ):
            raise ProjectError("Unsupported project schema_version (expected 1)")
        kind = document.get("kind")
        if kind not in KINDS:
            raise ProjectError("Project kind must be application, firmware or combined")
        application = (
            safe_relative(root, document.get("application_path"), allow_root=True)
            if kind != "firmware"
            else None
        )
        firmware = (
            safe_relative(root, document.get("firmware_path"))
            if kind != "application"
            else None
        )
        if kind == "firmware" and document.get("application_path") is not None:
            raise ProjectError("Firmware-only project cannot declare application_path")
        if kind == "application" and document.get("firmware_path") is not None:
            raise ProjectError("Application-only project cannot declare firmware_path")
    elif (root / "app.json").exists():
        kind, application, firmware = "application", root, None
    else:
        raise ProjectError(f"No {PROJECT_MANIFEST} or legacy app.json found: {root}")
    if application is not None:
        reject_symlinks(application / "app.json")
        try:
            ApplicationManifest.load(application)
        except (ApplicationManifestError, OSError, ValueError) as exc:
            raise ProjectError(f"Invalid application member: {exc}") from exc
    metadata = None
    build_directory = None
    if firmware is not None:
        raw = read_document(firmware / FIRMWARE_MANIFEST)
        if set(raw) - {"id", "name", "version", "description", "build_path", "inference_model", "icon"}:
            raise ProjectError("Unsupported firmware metadata fields")
        if any(
            not isinstance(raw.get(key), str) or not raw[key].strip()
            for key in ("id", "name", "version", "description")
        ):
            raise ProjectError(
                "Firmware metadata requires nonempty id/name/version/description strings"
            )
        firmware_id(raw["id"])
        version(raw["version"])
        build = safe_relative(firmware, raw.get("build_path", "build"), allow_root=True)
        build_directory = build if build.is_dir() else None
        metadata = {
            key: str(raw[key]) for key in ("id", "name", "version", "description")
        }
        inference_model = raw.get("inference_model", "")
        if not isinstance(inference_model, str) or len(inference_model) > 128:
            raise ProjectError("Invalid firmware inference_model")
        if "inference_model" in raw:
            metadata["inference_model"] = inference_model.strip()
        if raw.get("icon", "") != "":
            icon = safe_relative(firmware, raw["icon"])
            if not icon.is_file():
                raise ProjectError("Firmware icon must be an image file")
            with icon.open("rb") as stream:
                content = stream.read(MAX_FIRMWARE_ICON_BYTES + 1)
            url = image_data_url(content) if len(content) <= MAX_FIRMWARE_ICON_BYTES else None
            if not url:
                raise ProjectError("Firmware icon must be a supported image up to 16 KiB")
            metadata["icon_data_url"] = url
    return DevelopmentProject(
        str(kind), root, application, firmware, metadata, build_directory
    )


def inspect_project(directory: Path) -> dict[str, Any]:
    """Return JSON-serializable project information without starting a Daemon."""
    return load_project(directory).to_dict()
