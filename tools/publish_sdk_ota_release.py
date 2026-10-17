"""Verify signed SDK OTA metadata, then publish only from the protected CI job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from email.parser import Parser
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import httpx
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

REPOSITORY = "orulink-ai/WatcheRobot_python_sdk"


def require_publish_context(environment: Mapping[str, str]) -> None:
    expected = (
        REPOSITORY + "/.github/workflows/publish-sdk-ota-catalog.yml@refs/heads/main"
    )
    if (
        environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("GITHUB_REPOSITORY") != REPOSITORY
        or environment.get("GITHUB_REF") != "refs/heads/main"
        or environment.get("GITHUB_WORKFLOW_REF") != expected
        or not environment.get("ACTIONS_ID_TOKEN_REQUEST_URL")
        or not environment.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    ):
        raise ValueError(
            "SDK catalog publication requires the protected official workflow and OIDC"
        )


def validate_inputs(tag: str, version: str) -> None:
    if not tag.startswith("v"):
        raise ValueError("SDK tag must be v<stable version>")
    for value in (tag[1:], version):
        parsed = Version(value)
        if (
            str(parsed) != value
            or parsed.is_prerelease
            or parsed.is_devrelease
            or parsed.local
        ):
            raise ValueError(
                "SDK and catalog versions must be canonical stable versions"
            )


def verify_catalog_wheel(sdk_wheel: Path, catalog_wheel: Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "src"))
    from tools.verify_sdk_management_release import verify_wheel
    from watcherobot.management.releases import MAX_CATALOG_BYTES, verify_catalog

    sdk = verify_wheel(sdk_wheel, require_update_trust=True)
    name, version, _, tags = parse_wheel_filename(catalog_wheel.name)
    if canonicalize_name(name) != "watcherobot-desktop-release" or {
        str(tag) for tag in tags
    } != {"py3-none-any"}:
        raise ValueError("Expected the pure-data SDK catalog distribution")
    validate_inputs("v" + sdk["sdk_version"], str(version))
    with ZipFile(sdk_wheel) as archive:
        keys = json.loads(archive.read("watcherobot/management/release-keys.json"))
    with ZipFile(catalog_wheel) as archive:
        dist = f"watcherobot_desktop_release-{version}.dist-info"
        expected = {
            "watcherobot_desktop_release/__init__.py",
            "watcherobot_desktop_release/catalog.json",
            dist + "/METADATA",
            dist + "/WHEEL",
            dist + "/RECORD",
        }
        if (
            len(archive.namelist()) != len(expected)
            or set(archive.namelist()) != expected
            or archive.read("watcherobot_desktop_release/__init__.py")
        ):
            raise ValueError("SDK catalog must contain data only")
        if (
            archive.getinfo("watcherobot_desktop_release/catalog.json").file_size
            > MAX_CATALOG_BYTES
        ):
            raise ValueError("SDK catalog exceeds limit")
        metadata = Parser().parsestr(archive.read(dist + "/METADATA").decode("utf-8"))
        if (
            canonicalize_name(metadata.get("Name", "")) != "watcherobot-desktop-release"
            or Version(metadata.get("Version", "")) != version
        ):
            raise ValueError("Catalog wheel metadata differs from its filename")
        catalog = verify_catalog(
            archive.read("watcherobot_desktop_release/catalog.json"), keys
        )
    matching = [
        release for release in catalog.releases if release.version == sdk["sdk_version"]
    ]
    if not matching:
        raise ValueError("Catalog does not describe this SDK wheel")
    for release in matching:
        artifact = next(
            item
            for item in release.artifacts
            if canonicalize_name(item.name) == "watcherobot"
        )
        if (
            artifact.filename != sdk_wheel.name
            or artifact.sha256 != sdk["sha256"]
            or artifact.size != sdk_wheel.stat().st_size
        ):
            raise ValueError("Catalog SDK wheel differs from the immutable release")
    return {
        **sdk,
        "catalog_version": str(version),
        "catalog_sha256": hashlib.sha256(catalog_wheel.read_bytes()).hexdigest(),
        "sequence": catalog.sequence,
        "platforms": sorted(release.platform for release in matching),
    }


def registry_file(project: str, version: str, filename: str) -> dict[str, Any]:
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Registry response exceeds limit")
    files = json.loads(raw)["urls"]
    found = [item for item in files if item["filename"] == filename]
    if len(found) != 1:
        raise ValueError("Published registry artifact is missing or ambiguous")
    return found[0]


def validate_publication_order(proof: dict[str, Any], previous: dict[str, Any]) -> None:
    current, old = (
        Version(proof["catalog_version"]),
        Version(previous["catalog_version"]),
    )
    if current < old:
        raise ValueError("Catalog version must not regress")
    if current == old:
        if (
            proof["catalog_sha256"] != previous["catalog_sha256"]
            or proof["sequence"] != previous["sequence"]
        ):
            raise ValueError("Existing catalog versions are immutable")
    elif proof["sequence"] <= previous["sequence"]:
        raise ValueError("Catalog sequence must increase before publication")


def previous_publication(sdk_wheel: Path) -> dict[str, Any] | None:
    import io
    from urllib.parse import urlsplit

    from watcherobot.management.releases import verify_catalog

    try:
        with urllib.request.urlopen(
            "https://pypi.org/pypi/watcherobot-desktop-release/json", timeout=30
        ) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Registry response exceeds limit")
    versions = [
        Version(version)
        for version, files in json.loads(raw)["releases"].items()
        if files
    ]
    stable = [
        version
        for version in versions
        if not version.is_prerelease and not version.is_devrelease
    ]
    if not stable:
        return None
    version = str(max(stable))
    filename = f"watcherobot_desktop_release-{version}-py3-none-any.whl"
    registered = registry_file("watcherobot-desktop-release", version, filename)
    parsed = urlsplit(registered["url"])
    if (
        parsed.scheme != "https"
        or parsed.hostname != "files.pythonhosted.org"
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Registry returned an untrusted artifact URL")
    with urllib.request.urlopen(registered["url"], timeout=30) as response:
        artifact = response.read(4 * 1024 * 1024 + 1)
    if (
        len(artifact) > 4 * 1024 * 1024
        or hashlib.sha256(artifact).hexdigest() != registered["digests"]["sha256"]
    ):
        raise ValueError("Previous catalog differs from the registry artifact")
    with ZipFile(sdk_wheel) as archive:
        keys = json.loads(archive.read("watcherobot/management/release-keys.json"))
    with ZipFile(io.BytesIO(artifact)) as archive:
        catalog = verify_catalog(
            archive.read("watcherobot_desktop_release/catalog.json"), keys
        )
    return {
        "catalog_version": version,
        "catalog_sha256": registered["digests"]["sha256"],
        "sequence": catalog.sequence,
    }


def publish(sdk_wheel: Path, catalog_wheel: Path, proof: dict[str, Any]) -> None:
    require_publish_context(os.environ)
    release = json.loads(
        subprocess.run(
            [
                "gh",
                "release",
                "view",
                "v" + proof["sdk_version"],
                "--repo",
                REPOSITORY,
                "--json",
                "tagName,isDraft,isPrerelease",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    if (
        release["tagName"] != "v" + proof["sdk_version"]
        or release["isDraft"]
        or release["isPrerelease"]
    ):
        raise ValueError("The SDK stable release must be verified and public first")
    registered = registry_file("watcherobot", proof["sdk_version"], sdk_wheel.name)
    if (
        registered["digests"]["sha256"] != proof["sha256"]
        or registered["size"] != sdk_wheel.stat().st_size
    ):
        raise ValueError("SDK wheel does not match the immutable PyPI release")
    previous = previous_publication(sdk_wheel)
    if previous is not None:
        validate_publication_order(proof, previous)
    # Explicit OIDC and registry arguments prevent developer credential/config fallback.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("UV_PUBLISH_")
    }
    subprocess.run(
        [
            "uv",
            "--no-config",
            "publish",
            "--trusted-publishing",
            "always",
            "--publish-url",
            "https://upload.pypi.org/legacy/",
            "--check-url",
            "https://pypi.org/simple/",
            str(catalog_wheel),
        ],
        env=environment,
        check=True,
    )
    from urllib.parse import urlsplit

    from watcherobot.management.mirror import Mirror
    from watcherobot.management.releases import verify_catalog

    with ZipFile(sdk_wheel) as archive:
        keys = json.loads(archive.read("watcherobot/management/release-keys.json"))
    last: Exception | None = None
    for attempt in range(12):
        try:
            registered = registry_file(
                "watcherobot-desktop-release",
                proof["catalog_version"],
                catalog_wheel.name,
            )
            if registered["digests"]["sha256"] != proof["catalog_sha256"]:
                raise ValueError(
                    "Published SDK catalog differs from the prepared artifact"
                )
            url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/" + urlsplit(
                registered["url"]
            ).path.lstrip("/")
            with Mirror() as mirror:
                raw = mirror.fetch(url, 4 * 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() != proof["catalog_sha256"]:
                raise ValueError(
                    "Mirrored SDK catalog differs from the published artifact"
                )
            import io

            with ZipFile(io.BytesIO(raw)) as archive:
                verify_catalog(
                    archive.read("watcherobot_desktop_release/catalog.json"), keys
                )
            return
        except (OSError, httpx.HTTPError) as exc:
            last = exc
            if attempt < 11:
                time.sleep(10)
    raise RuntimeError(
        "Catalog publication completed but mirror verification is pending; retry the same immutable artifact"
    ) from last


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-wheel", type=Path)
    parser.add_argument("--catalog-wheel", type=Path)
    parser.add_argument("--sdk-tag")
    parser.add_argument("--catalog-version")
    parser.add_argument("--validate-inputs", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    if args.validate_inputs:
        if not args.sdk_tag or not args.catalog_version:
            parser.error("validation requires --sdk-tag and --catalog-version")
        validate_inputs(args.sdk_tag, args.catalog_version)
        return 0
    if not args.sdk_wheel or not args.catalog_wheel:
        parser.error("--sdk-wheel and --catalog-wheel are required")
    proof = verify_catalog_wheel(args.sdk_wheel, args.catalog_wheel)
    if args.sdk_tag and args.sdk_tag != "v" + proof["sdk_version"]:
        raise ValueError("SDK tag differs from the supplied wheel")
    if args.catalog_version and args.catalog_version != proof["catalog_version"]:
        raise ValueError("Catalog version differs from the supplied wheel")
    if args.publish:
        publish(args.sdk_wheel, args.catalog_wheel, proof)
    print(json.dumps({**proof, "published": args.publish}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
