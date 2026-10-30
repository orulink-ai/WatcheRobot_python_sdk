"""Bounded Desktop operations with durable receipts; no business-frame routing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any


from watcherobot.distribution.credentials import SystemCredentialStore
from watcherobot.distribution.providers import get_provider

from .catalog import (
    FirmwareRelease,
    parse_catalog,
    resolve_release,
)
from .contract import FirmwareError, FirmwareRequirement, version
from .platforms import FirmwarePlatform
from .transfer import Progress, acquire_package, release_record

ACTIONS = {
    "marketplace",
    "download",
    "publish",
    "submit",
    "requirements",
    "history",
    "configure",
    "status",
}


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _releases(root: Path, provider: str) -> tuple[FirmwareRelease, ...]:
    try:
        return parse_catalog(
            (root / provider / "catalog.json").read_bytes(), provider=provider
        )
    except OSError:
        raise FirmwareError(
            "Refresh the firmware catalog before selecting a release"
        ) from None


def _selected(
    request: dict[str, Any], releases: tuple[FirmwareRelease, ...]
) -> FirmwareRelease:
    matches = [
        release
        for release in releases
        if release.firmware_id == request.get("firmware_id")
        and version(release.version) == version(request.get("version"))
        and not release.withdrawn
    ]
    if len(matches) != 1:
        raise FirmwareError("Selected firmware is missing, withdrawn or ambiguous")
    return matches[0]


def _app_requirement(root: Path, app_id: Any) -> dict[str, object] | None:
    if (
        not isinstance(app_id, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", app_id) is None
    ):
        raise FirmwareError("Invalid Application ID")
    source = (root / "apps" / app_id / "source").resolve()
    if not source.is_relative_to(root.resolve()):
        raise FirmwareError("Application path is outside store")
    from watcherobot.runtime.daemon.application.manifest import (
        parse_application_manifest,
    )

    metadata = parse_application_manifest((source / "app.json").read_bytes())
    return metadata.requires_firmware.to_dict() if metadata.requires_firmware else None


def execute(
    action: str,
    request: dict[str, Any],
    *,
    provider: str,
    cache_root: Path,
    store_root: Path,
    progress: Progress,
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise FirmwareError("Unsupported firmware operation")
    get_provider(provider)
    root = Path(cache_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    backend = FirmwarePlatform(provider)
    if action == "configure":
        from watcherobot.runtime.daemon.application.manifest import (
            parse_application_manifest,
        )

        path = Path(request["application_dir"]).resolve() / "app.json"
        original = path.read_bytes()
        parse_application_manifest(original)
        requirement = FirmwareRequirement.parse(request.get("requires_firmware"))
        if requirement is None:
            raise FirmwareError("Choose an explicit firmware requirement")
        if requirement.mode == "required":
            resolve_release(requirement, _releases(root, provider), provider=provider)
        value = json.loads(original)
        value.update(requires_firmware=requirement.to_dict())
        parse_application_manifest(json.dumps(value).encode())
        preview_key = hashlib.sha256(
            original + json.dumps(value, sort_keys=True).encode()
        ).hexdigest()
        if request.get("confirmed") is not True:
            return {"preview": value, "preview_key": preview_key}
        if request.get("preview_key") != preview_key or path.read_bytes() != original:
            raise FirmwareError(
                "Application manifest changed while preparing the dependency"
            )
        _write(path, value)
        return {"configured": True, "requires_firmware": requirement.to_dict()}
    if action == "marketplace":
        progress(stage="fetching_catalog")
        warnings = []
        commit = None
        try:
            commit, releases = backend.catalog()
            _write(
                root / provider / "catalog.json",
                {
                    "schema_version": 1,
                    "firmwares": [release_record(item) for item in releases],
                },
            )
        except Exception:
            warnings.append("firmware_catalog_unavailable")
            try:
                releases = _releases(root, provider)
            except (FirmwareError, OSError):
                releases = ()
        return {
            "catalog_commit": commit,
            "firmwares": [asdict(item) for item in releases if not item.withdrawn],
            "warnings": warnings,
            "stale": bool(warnings),
        }
    if action == "requirements":
        return {
            "requires_firmware": _app_requirement(store_root, request.get("app_id"))
        }
    if action == "status":
        import time
        from .status import firmware_status

        try:
            releases = _releases(root, provider)
        except FirmwareError:
            releases = ()
        receipt = None
        robot_key = request.get("device_key")
        if isinstance(robot_key, str) and robot_key:
            jobs = execute(
                "history",
                {},
                provider=provider,
                cache_root=root,
                store_root=store_root,
                progress=progress,
            )["jobs"]
            receipt = next(
                (item for item in jobs if item.get("robot_key") == robot_key), None
            )
        result = firmware_status(
            request.get("observation"), releases, now=time.time(), last_write=receipt
        )
        raw = (
            _app_requirement(store_root, request["app_id"])
            if request.get("app_id")
            else request.get("requires_firmware")
        )
        requirement = FirmwareRequirement.parse(raw)
        result["compatibility"] = "unknown"
        if requirement and requirement.mode == "not_required":
            result["compatibility"] = "not_required"
        elif requirement and result["evidence"] == "wifi" and result["firmware_id"]:
            result["compatibility"] = (
                "compatible"
                if requirement.accepts(result["firmware_id"], result["version"])
                else "incompatible"
            )
        return result
    if action == "history":
        records = []
        for path in sorted(
            (root / "jobs").glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:30]:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(item, dict):
                records.append(item)
        return {"jobs": records}
    if action == "publish":
        token = SystemCredentialStore(provider=provider).load()
        if token is None:
            raise FirmwareError("Sign in to the selected provider before publishing")
        release = backend.publish(
            Path(request["package_path"]), token, progress=progress
        )
        # Verify public accessibility and exact bytes, not only authenticated upload success.
        with tempfile.TemporaryDirectory(prefix="firmware-public-check-") as temporary:
            acquire_package(release, Path(temporary), progress=progress)
        key = release.sha256
        _write(
            root / provider / "publications" / (key + ".json"),
            {"schema_version": 1, "firmwares": [release_record(release)]},
        )
        return {"publication_key": key, "release": asdict(release)}
    if action == "submit":
        key = request.get("publication_key", "")
        if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise FirmwareError("Invalid publication receipt")
        release = parse_catalog(
            (root / provider / "publications" / (key + ".json")).read_bytes(),
            provider=provider,
        )[0]
        token = SystemCredentialStore(provider=provider).load()
        if token is None:
            raise FirmwareError("Sign in before submitting firmware")
        return backend.submit(
            release, token, cache_root=root / "packages", progress=progress
        )
    releases = _releases(root, provider)
    if request.get("app_id"):
        raw = _app_requirement(store_root, request["app_id"])
        requirement = FirmwareRequirement.parse(raw)
        if requirement is None or requirement.mode == "not_required":
            return {
                "required": False,
                "evidence": "legacy_unknown" if requirement is None else "not_required",
            }
        release = resolve_release(requirement, releases, provider=provider)
    else:
        release = _selected(request, releases)
    if action == "download":
        path = acquire_package(release, root / "packages", progress=progress)
        return {"release": asdict(release), "package_path": str(path)}
    raise FirmwareError("USB device operations belong to Watcher Desktop")
