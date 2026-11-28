"""Offline project scaffolding, reusing the existing Application generator."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from watcherobot.application.project import (
    default_application_project_metadata,
    init_application_project,
)
from watcherobot.project.model import (
    FIRMWARE_MANIFEST,
    KINDS,
    PROJECT_MANIFEST,
    DevelopmentProject,
    ProjectError,
    load_project,
    reject_symlinks,
)


def _write_json(path: Path, document: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _make_member(target: Path, kind: str, *, project_name: str) -> None:
    defaults = default_application_project_metadata(Path(project_name))
    if kind == "application":
        init_application_project(
            target,
            app_id=defaults.app_id,
            name=defaults.name,
            author=defaults.author,
            description=defaults.description,
            supported_host_platforms=["windows", "macos"],
        )
    else:
        target.mkdir()
        icon = files("watcherobot") / "templates" / "base" / "icon.svg"
        (target / "icon.svg").write_bytes(icon.read_bytes())
        _write_json(
            target / FIRMWARE_MANIFEST,
            {
                "id": defaults.app_id[:55].rstrip("._-") + ".firmware",
                "name": defaults.name + " Firmware",
                "version": "0.1.0",
                "description": defaults.name + " device firmware project.",
                "icon": "icon.svg",
                "inference_model": "",
                "build_path": "build",
            },
        )
        (target / "README.md").write_text(
            "# Firmware project\n\nPlace your device firmware source here. Obtain the C SDK separately; "
            "this template does not clone it or install dependencies.\n\n"
            "Build using your firmware toolchain, then prepare the build directory with "
            "`watcherobot firmware prepare`. Optional `build_path` in `firmware.project.json` "
            "is relative to this directory (default: `build`). A missing build directory is valid before "
            "compilation. Firmware metadata does not imply a published release.\n\n"
            "Set your firmware ID, display name, version and description in firmware.project.json. "
            "Optionally fill inference_model with the name of the model your firmware uses on-device, "
            "for example face-detection-v2. Leave it empty if no model is used. "
            "Replace icon.svg or set icon to a project-relative image (16 KiB maximum). "
            "Run watcherobot firmware prepare on the project root to include these fields.\n\n"
            "## Marketplace publication\n\n"
            "Publish a complete installation build directory or ZIP, including the generated "
            "flash file mapping, bootloader, partition-table binary, application images and "
            "any startup/data images your firmware needs. The SDK extracts addresses, Flash "
            "settings, partition layout, sizes and hashes automatically; only supplement "
            "display metadata such as name, version and description. Custom partition names, "
            "counts and sizes are supported within the target hardware limits.\n\n"
            "An application-update package alone cannot be published or submitted to the "
            "marketplace; it remains usable locally with a compatible existing layout. "
            "`--mode full` cannot supply missing build files. Verify a complete installation "
            "and restart on the supported hardware before submission, including any required "
            "OTA startup data. The desktop flashes the package's declared layout; users "
            "do not choose partitions or an installation mode.\n\n"
            "For a combined project, configure the Application `requires_firmware` only after "
            "publishing real compatible firmware through the same provider. No dependency is "
            "fabricated by this template.\n",
            encoding="utf-8",
        )


def init_project(directory: Path, *, kind: str) -> DevelopmentProject:
    if kind not in KINDS:
        raise ProjectError(f"Unknown project kind: {kind}")
    target = Path(directory).absolute()
    reject_symlinks(target)
    if target.exists():
        raise ProjectError(f"Target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".watcherobot-project-", dir=target.parent
    ) as temporary:
        staging = Path(temporary) / "project"
        staging.mkdir()
        document: dict[str, Any] = {"schema_version": 1, "kind": kind}
        for member in ("application", "firmware"):
            if kind in (member, "combined"):
                _make_member(staging / member, member, project_name=target.name)
                document[member + "_path"] = member
        _write_json(staging / PROJECT_MANIFEST, document)
        load_project(staging)
        # Exclusive reservation works on Windows and POSIX, even for empty targets.
        target.mkdir()
        try:
            for child in staging.iterdir():
                child.rename(target / child.name)
        except BaseException:
            shutil.rmtree(target)
            raise
    return load_project(target)


def add_project_member(directory: Path, *, kind: str) -> DevelopmentProject:
    if kind not in ("application", "firmware"):
        raise ProjectError("Member kind must be application or firmware")
    current = load_project(directory)
    root = current.project_root
    lock = root / ".watcherobot-project.lock"
    # Serialize SDK writers; never overwrite an existing lock or stale file.
    try:
        with lock.open("x", encoding="utf-8"):
            pass
    except FileExistsError as exc:
        raise ProjectError(
            "Project is being modified; existing lock must be resolved first"
        ) from exc
    try:
        return _add_locked(root, kind=kind)
    finally:
        lock.unlink()


def _add_locked(root: Path, *, kind: str) -> DevelopmentProject:
    current = load_project(root)
    if getattr(current, kind + "_directory") is not None:
        raise ProjectError(f"Project already has a {kind} member")
    member = root / kind
    reject_symlinks(member)
    if member.exists():
        raise ProjectError(f"Target already exists: {member}")
    manifest = root / PROJECT_MANIFEST
    original = manifest.read_bytes() if manifest.exists() else None
    document: dict[str, Any] = {"schema_version": 1, "kind": "combined"}
    for name in ("application", "firmware"):
        existing = getattr(current, name + "_directory")
        document[name + "_path"] = (
            existing.relative_to(root).as_posix() if existing is not None else name
        )
    with tempfile.TemporaryDirectory(
        prefix=".watcherobot-member-", dir=root
    ) as temporary:
        staging = Path(temporary)
        _make_member(staging / kind, kind, project_name=root.name)
        _write_json(staging / PROJECT_MANIFEST, document)
        member.mkdir()  # exclusive; no replace of an existing directory
        try:
            for child in (staging / kind).iterdir():
                child.rename(member / child.name)
            reject_symlinks(manifest)
            if (manifest.read_bytes() if manifest.exists() else None) != original:
                raise ProjectError("Project metadata changed while adding member")
            if original is None:
                # Atomic creation without replacing a concurrently created manifest.
                os.link(staging / PROJECT_MANIFEST, manifest)
            else:
                os.replace(staging / PROJECT_MANIFEST, manifest)
        except BaseException:
            shutil.rmtree(member)
            raise
    return load_project(root)
