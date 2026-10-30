"""Strict firmware identity and deliberately limited release version rules."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_COMPARISON = re.compile(
    r"(==|>=|<=|>|<)(v?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
)


class FirmwareError(ValueError):
    """Invalid firmware metadata; no hardware operation may follow."""


def firmware_id(value: object) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise FirmwareError("Invalid firmware ID")
    return value


def version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or len(value) > 48:
        raise FirmwareError("Firmware version must use three release numbers")
    match = _VERSION.fullmatch(value)
    if match is None:
        raise FirmwareError("Firmware version must use three release numbers")
    return int(match[1]), int(match[2]), int(match[3])


def validate_range(spec: object) -> str:
    if not isinstance(spec, str) or not spec or len(spec) > 256:
        raise FirmwareError("Invalid firmware version range")
    for part in spec.split(","):
        match = _COMPARISON.fullmatch(part.strip())
        if match is None:
            raise FirmwareError("Unsupported firmware version comparison")
        version(match[2])
    return spec


def contains(spec: str, candidate: str) -> bool:
    validate_range(spec)
    actual = version(candidate)
    for part in spec.split(","):
        match = _COMPARISON.fullmatch(part.strip())
        assert match is not None
        target = version(match[2])
        accepted = {
            "==": actual == target,
            ">=": actual >= target,
            "<=": actual <= target,
            ">": actual > target,
            "<": actual < target,
        }
        if not accepted[match[1]]:
            return False
    return True


@dataclass(frozen=True)
class FirmwareRequirement:
    mode: str
    firmware_id: str = ""
    version_spec: str = ""
    recommended_version: str = ""
    tested_versions: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: object) -> FirmwareRequirement | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise FirmwareError("requires_firmware must be an object")
        if value == {"mode": "not_required"}:
            return cls(mode="not_required")
        fields = {
            "mode",
            "firmware_id",
            "version_spec",
            "recommended_version",
            "tested_versions",
        }
        if set(value) != fields or value["mode"] != "required":
            raise FirmwareError("Invalid required firmware fields")
        identity = firmware_id(value["firmware_id"])
        spec = validate_range(value["version_spec"])
        recommended = value["recommended_version"]
        version(recommended)
        if not contains(spec, recommended):
            raise FirmwareError(
                "Recommended firmware is outside the compatibility range"
            )
        tested = value["tested_versions"]
        if not isinstance(tested, list) or len(tested) > 100:
            raise FirmwareError("tested_versions must be a bounded list")
        normalized = []
        for item in tested:
            if not contains(spec, item):
                raise FirmwareError(
                    "Tested firmware is outside the compatibility range"
                )
            normalized.append(version(item))
        if len(set(normalized)) != len(normalized):
            raise FirmwareError("Duplicate tested firmware version")
        return cls("required", identity, spec, recommended, tuple(tested))

    def accepts(self, identity: str, candidate: str) -> bool:
        return (
            self.mode == "required"
            and identity == self.firmware_id
            and contains(self.version_spec, candidate)
        )

    def to_dict(self) -> dict[str, object]:
        if self.mode == "not_required":
            return {"mode": self.mode}
        return {
            "mode": self.mode,
            "firmware_id": self.firmware_id,
            "version_spec": self.version_spec,
            "recommended_version": self.recommended_version,
            "tested_versions": list(self.tested_versions),
        }
