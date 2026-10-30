"""Readback evidence and reviewed origin policy; never infer identity from a version."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .catalog import FirmwareRelease
from .contract import FirmwareError, firmware_id, version


def firmware_status(
    observation: dict[str, Any] | None,
    releases: Sequence[FirmwareRelease],
    *,
    now: float,
    last_write: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "version": None,
        "firmware_id": None,
        "evidence": "unknown",
        "runtime_version_verified": False,
        "official": False,
        "ota_allowed": False,
        "last_write": last_write,
    }
    if last_write and last_write.get("status") == "succeeded":
        value = last_write.get("release", {})
        try:
            version(value.get("version"))
            previous_identity = firmware_id(value.get("firmware_id"))
        except (FirmwareError, AttributeError):
            pass
        else:
            result.update(
                version=value["version"],
                firmware_id=previous_identity,
                evidence="last_write",
            )
    if not isinstance(observation, dict):
        return result
    received = observation.get("observed_at")
    if (
        not isinstance(received, (int, float))
        or isinstance(received, bool)
        or not -3 <= now - received <= 30
    ):
        return result
    try:
        parsed = version(observation.get("firmware_version"))
    except FirmwareError:
        return result
    identity = observation.get("firmware_id")
    try:
        identity = firmware_id(identity)
    except FirmwareError:
        identity = None
    result.update(
        version=observation["firmware_version"],
        firmware_id=identity,
        evidence="wifi",
        runtime_version_verified=True,
    )
    for release in releases:
        if (
            release.firmware_id != identity
            or version(release.version) != parsed
            or release.withdrawn
        ):
            continue
        owner = release.repo_id.split("/", 1)[0].casefold()
        official = (
            release.publisher_kind == "official"
            and owner
            == {"huggingface": "orulink", "gitee": "orulink-sz"}[release.provider]
        )
        result.update(official=official, ota_allowed=official and release.ota_supported)
        break
    return result
