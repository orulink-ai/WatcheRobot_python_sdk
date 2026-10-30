"""Separate firmware catalog; never broadens the legacy app-list contract."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .contract import FirmwareError, FirmwareRequirement, firmware_id, version
from .package import MAX_PACKAGE_BYTES, _integer, _pairs, _path

CATALOG_PATH = "firmware-list.json"
MAX_CATALOG_BYTES = 1024 * 1024
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class FirmwareRelease:
    provider: str
    firmware_id: str
    version: str
    repo_id: str
    commit: str
    size: int
    sha256: str
    withdrawn: bool
    path: str = ""
    asset_id: int | None = None
    download_url: str = ""
    name: str = ""
    description: str = ""
    author: str = ""
    notes: str = ""
    skus: tuple[str, ...] = ()
    hardware_revisions: tuple[str, ...] = ()
    publisher_kind: str = "community"
    ota_supported: bool = False


def parse_catalog(document: bytes, *, provider: str) -> tuple[FirmwareRelease, ...]:
    if provider not in {"huggingface", "gitee"} or len(document) > MAX_CATALOG_BYTES:
        raise FirmwareError("Unsupported firmware catalog")
    try:
        payload = json.loads(document.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FirmwareError("Invalid firmware catalog document") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "firmwares"}
        or (
            type(payload["schema_version"]) is not int or payload["schema_version"] != 1
        )
    ):
        raise FirmwareError("Invalid firmware catalog schema")
    entries = payload["firmwares"]
    if not isinstance(entries, list) or len(entries) > 1000:
        raise FirmwareError("Invalid firmware release list")
    result = []
    identities = set()
    common = {
        "firmware_id",
        "version",
        "repo_id",
        "commit",
        "size",
        "sha256",
        "withdrawn",
    }
    for entry in entries:
        required = common | (
            {"path"} if provider == "huggingface" else {"asset_id", "download_url"}
        )
        display_fields = {
            "name",
            "description",
            "author",
            "notes",
            "skus",
            "hardware_revisions",
            "publisher_kind",
            "ota_supported",
        }
        if (
            not isinstance(entry, dict)
            or not required.issubset(entry)
            or set(entry) - required - display_fields
        ):
            raise FirmwareError("Invalid firmware catalog fields")
        display: dict[str, Any] = {}
        kind = entry.get("publisher_kind", "community")
        ota = entry.get("ota_supported", False)
        if kind not in {"official", "community"} or type(ota) is not bool:
            raise FirmwareError("Invalid firmware publisher or OTA policy")
        display.update(publisher_kind=kind, ota_supported=ota)
        for field in ("name", "description", "author", "notes"):
            value = entry.get(field, "")
            if not isinstance(value, str) or len(value) > 4000:
                raise FirmwareError("Invalid firmware display information")
            display[field] = value
        for field in ("skus", "hardware_revisions"):
            value = entry.get(field, [])
            if (
                not isinstance(value, (tuple, list))
                or len(value) > 64
                or not all(
                    isinstance(item, str) and 0 < len(item) <= 64 for item in value
                )
            ):
                raise FirmwareError("Invalid firmware hardware information")
            display[field] = tuple(value)
        identity = firmware_id(entry["firmware_id"])
        parsed_version = version(entry["version"])
        key = identity, parsed_version
        if key in identities:
            raise FirmwareError("Duplicate firmware version")
        identities.add(key)
        repo, commit, digest = entry["repo_id"], entry["commit"], entry["sha256"]
        if (
            not isinstance(repo, str)
            or len(repo) > 200
            or _REPO.fullmatch(repo) is None
        ):
            raise FirmwareError("Invalid firmware repository")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            raise FirmwareError("Firmware revision must be a fixed commit")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise FirmwareError("Invalid firmware package digest")
        size = _integer(entry["size"], minimum=1, maximum=MAX_PACKAGE_BYTES)
        if type(entry["withdrawn"]) is not bool:
            raise FirmwareError("Invalid release withdrawal state")
        path, asset_id, download_url = "", None, ""
        if provider == "huggingface":
            path = _path(entry["path"])
            if not path.endswith(".zip"):
                raise FirmwareError("Firmware artifact must be a ZIP")
        else:
            asset_id = _integer(entry["asset_id"], minimum=1, maximum=2**53 - 1)
            download_url = entry["download_url"]
            if not isinstance(download_url, str) or len(download_url) > 2048:
                raise FirmwareError("Invalid firmware asset URL")
            parsed = urlsplit(download_url)
            browser_path = parsed.path.startswith(
                f"/{repo}/releases/download/"
            ) and parsed.path.endswith(".zip")
            api_path = (
                re.fullmatch(
                    re.escape(f"/api/v5/repos/{repo}/releases/")
                    + r"[1-9][0-9]*/attach_files/"
                    + str(asset_id)
                    + r"/download",
                    parsed.path,
                )
                is not None
            )
            if (
                parsed.scheme != "https"
                or parsed.netloc != "gitee.com"
                or parsed.query
                or parsed.fragment
                or not (browser_path or api_path)
            ):
                raise FirmwareError("Firmware asset URL must belong to its repository")
            _path(parsed.path.lstrip("/"))
        result.append(
            FirmwareRelease(
                provider,
                identity,
                entry["version"],
                repo,
                commit,
                size,
                digest,
                entry["withdrawn"],
                path,
                asset_id,
                download_url,
                **display,
            )
        )
    return tuple(result)


def resolve_release(
    requirement: FirmwareRequirement,
    releases: tuple[FirmwareRelease, ...],
    *,
    provider: str,
) -> FirmwareRelease:
    """Resolve the declared recommendation, without latest or cross-source fallback."""
    if requirement.mode != "required":
        raise FirmwareError("This application does not require a firmware release")
    for release in releases:
        if (
            release.provider == provider
            and not release.withdrawn
            and (
                release.firmware_id == requirement.firmware_id
                and version(release.version) == version(requirement.recommended_version)
                and requirement.accepts(release.firmware_id, release.version)
            )
        ):
            return release
    raise FirmwareError("Recommended firmware is not available in the selected catalog")
