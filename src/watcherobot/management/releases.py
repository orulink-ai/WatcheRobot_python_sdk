"""Signed, platform-specific SDK locks; no GitHub source or runtime resolution."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.specifiers import SpecifierSet
from packaging.utils import (
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import Version

from .compatibility import check_desktop

_MIRROR_HOST = "mirrors.tuna.tsinghua.edu.cn"
MAX_CATALOG_BYTES = 2 * 1024 * 1024


def validate_artifact_url(url: str) -> str:
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _MIRROR_HOST
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not path.startswith("/pypi/web/")
        or "\\" in path
        or any(part in (".", "..") for part in path.split("/"))
    ):
        raise ValueError(
            "SDK downloads must remain on the configured HTTPS Tsinghua mirror"
        )
    return url


@dataclass(frozen=True)
class Artifact:
    name: str
    version: str
    filename: str
    url: str
    sha256: str
    size: int

    @property
    def is_wheel(self) -> bool:
        return self.filename.endswith(".whl")


@dataclass(frozen=True)
class Release:
    version: str
    platform: str
    python: str
    contracts: dict[str, object]
    requirements: tuple[Artifact, ...]
    notes: str
    identity: str
    build_requirements: tuple[Artifact, ...] = ()

    @property
    def artifacts(self) -> tuple[Artifact, ...]:
        unique = {w.sha256: w for w in (*self.requirements, *self.build_requirements)}
        return tuple(unique.values())


@dataclass(frozen=True)
class Catalog:
    sequence: int
    releases: tuple[Release, ...]
    digest: str


def _artifact(item: dict[str, Any]) -> Artifact:
    name, version, filename = item["name"], item["version"], item["filename"]
    if filename.endswith(".whl"):
        wheel_name, wheel_version, _, _ = parse_wheel_filename(filename)
    else:
        wheel_name, wheel_version = parse_sdist_filename(filename)
    if (
        "/" in filename
        or "\\" in filename
        or wheel_name != canonicalize_name(name)
        or wheel_version != Version(version)
    ):
        raise ValueError("Artifact identity does not match its lock")
    if not re.fullmatch("[0-9a-f]{64}", item["sha256"]):
        raise ValueError("Artifact must have a SHA256 digest")
    if type(item["size"]) is not int or not 0 < item["size"] <= 1024**3:
        raise ValueError("Invalid locked wheel size")
    return Artifact(
        name,
        version,
        filename,
        validate_artifact_url(item["url"]),
        item["sha256"],
        item["size"],
    )


def _release(item: dict[str, Any]) -> Release:
    version = item["version"]
    Version(version)
    if item["platform"] not in {
        "darwin-arm64",
        "darwin-x64",
        "win32-x64",
        "win32-arm64",
        "linux-x64",
        "linux-arm64",
    }:
        raise ValueError("Unsupported release platform")
    if not isinstance(item["python"], str) or not item["python"]:
        raise ValueError("Python range is required")
    SpecifierSet(item["python"])
    contracts = item["contracts"]
    if set(contracts) != {
        "manager",
        "management",
        "distribution",
        "application",
        "state",
    } or any(type(v) is not int or v < 1 for v in contracts.values()):
        raise ValueError("Complete contract identity required")
    items = item["requirements"]
    if not isinstance(items, list) or not 1 <= len(items) <= 512:
        raise ValueError("Complete wheel lock required")
    wheels = tuple(_artifact(w) for w in items)
    names = {canonicalize_name(w.name) for w in wheels}
    if len(names) != len(wheels) or not any(
        canonicalize_name(w.name) == "watcherobot" and w.version == version
        for w in wheels
    ):
        raise ValueError("Lock must contain exactly one matching SDK")
    build_items = item.get("build_requirements", [])
    if not isinstance(build_items, list) or len(build_items) > 64:
        raise ValueError("Invalid locked build requirements")
    build_requirements = tuple(_artifact(w) for w in build_items)
    if any(not w.is_wheel for w in build_requirements):
        raise ValueError("Build tools must be locked wheels")
    if any(not w.is_wheel for w in wheels) and not build_requirements:
        raise ValueError("Source dependencies require complete locked build tools")
    if any(
        canonicalize_name(w.name) == "watcherobot" and not w.is_wheel for w in wheels
    ):
        raise ValueError("The SDK itself must be a published wheel")
    pins: dict[str, Artifact] = {}
    for artifact in (*wheels, *build_requirements):
        name = canonicalize_name(artifact.name)
        if name in pins and pins[name] != artifact:
            raise ValueError("Conflicting runtime and build dependency pins")
        pins[name] = artifact
    notes = item.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 16000:
        raise ValueError("Invalid release notes")
    identity = hashlib.sha256(
        json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return Release(
        version,
        item["platform"],
        item["python"],
        dict(contracts),
        wheels,
        notes,
        identity,
        build_requirements,
    )


def verify_catalog(blob: bytes, public_keys: Mapping[str, str]) -> Catalog:
    if len(blob) > MAX_CATALOG_BYTES:
        raise ValueError("SDK catalog exceeds limit")
    try:
        envelope = json.loads(blob)
        raw = base64.b64decode(envelope["payload"], validate=True)
        key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_keys[envelope["key_id"]], validate=True)
        )
        key.verify(base64.b64decode(envelope["signature"], validate=True), raw)
        payload = json.loads(raw)
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("Unsupported SDK catalog schema")
        sequence = payload["sequence"]
        if type(sequence) is not int or sequence < 1:
            raise ValueError("Invalid release sequence")
        items = payload["releases"]
        if not isinstance(items, list) or not 1 <= len(items) <= 200:
            raise ValueError("Invalid catalog release count")
        releases = tuple(_release(item) for item in items)
        if len({(r.platform, Version(r.version)) for r in releases}) != len(releases):
            raise ValueError("Duplicate SDK release")
        return Catalog(sequence, releases, hashlib.sha256(raw).hexdigest())
    except (KeyError, TypeError, InvalidSignature, UnicodeError) as exc:
        raise ValueError("Invalid or untrusted SDK release catalog") from exc


def compatible_releases(
    catalog: Catalog, requirements: Mapping[str, str], platform: str, python: str
) -> tuple[Release, ...]:
    return tuple(
        sorted(
            (
                r
                for r in catalog.releases
                if r.platform == platform
                and not Version(r.version).is_prerelease
                and not Version(r.version).is_devrelease
                and SpecifierSet(r.python).contains(python)
                and check_desktop(requirements, r.contracts).status == "compatible"
            ),
            key=lambda r: Version(r.version),
            reverse=True,
        )
    )


def recommended_release(
    catalog: Catalog, requirements: Mapping[str, str], platform: str, python: str
) -> Release | None:
    releases = compatible_releases(catalog, requirements, platform, python)
    return releases[0] if releases else None
