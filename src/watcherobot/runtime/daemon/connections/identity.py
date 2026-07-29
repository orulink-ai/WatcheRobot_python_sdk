"""Stable hardware identity helpers owned by the Daemon connection layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


IdentitySource = Literal["mac", "device_id", "client_id", "ip", "unknown"]
_PLACEHOLDER_VALUES = {"", "unknown", "none", "null", "undefined"}


@dataclass(frozen=True)
class DeviceIdentity:
    device_key: str
    identity_source: IdentitySource
    device_id: str = ""
    mac: str = ""


def normalize_device_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if normalized.lower() in _PLACEHOLDER_VALUES:
        return ""
    return normalized


def normalize_mac(value: object) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if raw.lower() in _PLACEHOLDER_VALUES:
        return ""
    hex_chars = re.sub(r"[^0-9a-fA-F]", "", raw)
    if len(hex_chars) == 12:
        upper = hex_chars.upper()
        if upper == "000000000000":
            return ""
        return ":".join(
            upper[index : index + 2]
            for index in range(0, 12, 2)
        )
    return raw.upper()


def resolve_device_identity(
    *,
    device_id: object = "",
    mac: object = "",
    connection_id: int | None = None,
) -> DeviceIdentity:
    normalized_mac = normalize_mac(mac)
    normalized_device_id = normalize_device_id(device_id)
    if normalized_mac:
        return DeviceIdentity(
            device_key=f"mac:{normalized_mac}",
            identity_source="mac",
            device_id=normalized_device_id,
            mac=normalized_mac,
        )
    if normalized_device_id:
        return DeviceIdentity(
            device_key=f"device_id:{normalized_device_id}",
            identity_source="device_id",
            device_id=normalized_device_id,
        )
    if connection_id is not None:
        return DeviceIdentity(
            device_key=f"client_id:{connection_id}",
            identity_source="client_id",
        )
    return DeviceIdentity(device_key="", identity_source="unknown")

def resolve_discovery_identity(
    *,
    device_id: object = "",
    mac: object = "",
    ip: object = "",
) -> DeviceIdentity:
    identity = resolve_device_identity(
        device_id=device_id,
        mac=mac,
    )
    if identity.device_key:
        return identity
    ip_text = str(ip or "").strip()
    if ip_text:
        return DeviceIdentity(
            device_key=f"ip:{ip_text}",
            identity_source="ip",
        )
    return DeviceIdentity(device_key="", identity_source="unknown")
