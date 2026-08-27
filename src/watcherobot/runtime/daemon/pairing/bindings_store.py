"""Durable daemon identity and the single remembered device binding.

The watcher-lan-pairing/1.1 fast reconnect design persists two things in the
runtime state root:

- ``daemon_instance_id`` so a restarted Daemon is recognisable by devices that
  already paired with its predecessor process, and
- exactly one device entry holding the HMAC binding secret established during
  manual pairing.

File layout follows the repository's ``RuntimeStateStore`` conventions: one
``device-bindings.json`` written atomically via ``.json.tmp`` + ``replace()``
with ``ensure_ascii=False``.  A missing or corrupt file yields a fresh
identity with no remembered device; deleting the file forgets everything.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, replace
from pathlib import Path

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STORE_VERSION = 1


@dataclass(frozen=True)
class DeviceBinding:
    binding_secret: str
    target_mode: str
    last_peer_ip: str
    last_ws_port: int
    paired_at_ms: int | None = None
    last_connected_at_ms: int | None = None


@dataclass(frozen=True)
class DeviceBindingsSnapshot:
    daemon_instance_id: str
    device: DeviceBinding | None


class DeviceBindingsStore:
    """Own ``<state root>/device-bindings.json`` for one Runtime process."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "device-bindings.json"

    def load(self) -> DeviceBindingsSnapshot:
        """Return the persisted identity, creating it on first use.

        Always leaves the file in a valid state afterwards: an absent or
        unreadable store is regenerated with a fresh instance id and no
        remembered device.
        """

        payload = self._read_payload()
        if payload is None:
            snapshot = DeviceBindingsSnapshot(
                daemon_instance_id=secrets.token_hex(16),
                device=None,
            )
            self.write(snapshot)
            return snapshot
        return payload

    def write(self, snapshot: DeviceBindingsSnapshot) -> None:
        if _HEX32.fullmatch(snapshot.daemon_instance_id) is None:
            raise ValueError("daemon_instance_id must be 32 lowercase hex characters")
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": _STORE_VERSION,
                    "daemon_instance_id": snapshot.daemon_instance_id,
                    "device": _device_to_json(snapshot.device),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def clear_device(self, *, now_ms: int | None = None) -> bool:
        """Forget the remembered device but keep this daemon's identity."""

        del now_ms  # informational only; kept for call-site symmetry
        snapshot = self.load()
        if snapshot.device is None:
            return False
        self.write(replace(snapshot, device=None))
        return True

    def _read_payload(self) -> DeviceBindingsSnapshot | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or raw.get("version") != _STORE_VERSION:
            return None
        instance_id = raw.get("daemon_instance_id")
        if not isinstance(instance_id, str) or _HEX32.fullmatch(instance_id) is None:
            return None
        return DeviceBindingsSnapshot(
            daemon_instance_id=instance_id,
            device=_device_from_json(raw.get("device")),
        )


def _device_to_json(device: DeviceBinding | None) -> dict[str, object] | None:
    if device is None:
        return None
    if _HEX64.fullmatch(device.binding_secret) is None:
        raise ValueError("binding_secret must be 64 lowercase hex characters")
    return {
        "binding_secret": device.binding_secret,
        "target_mode": device.target_mode,
        "last_peer_ip": device.last_peer_ip,
        "last_ws_port": device.last_ws_port,
        "paired_at_ms": device.paired_at_ms,
        "last_connected_at_ms": device.last_connected_at_ms,
    }


def _device_from_json(value: object) -> DeviceBinding | None:
    if not isinstance(value, dict):
        return None
    try:
        secret = value["binding_secret"]
        target_mode = value["target_mode"]
        peer_ip = value["last_peer_ip"]
        ws_port = value["last_ws_port"]
    except KeyError:
        return None
    if (
        not isinstance(secret, str)
        or _HEX64.fullmatch(secret) is None
        or not isinstance(target_mode, str)
        or not isinstance(peer_ip, str)
        or type(ws_port) is not int
        or not 1 <= ws_port <= 65535
    ):
        return None
    return DeviceBinding(
        binding_secret=secret,
        target_mode=target_mode,
        last_peer_ip=peer_ip,
        last_ws_port=ws_port,
        paired_at_ms=_optional_int(value.get("paired_at_ms")),
        last_connected_at_ms=_optional_int(value.get("last_connected_at_ms")),
    )


def _optional_int(value: object) -> int | None:
    return value if type(value) is int else None


def utc_now_ms() -> int:
    """Informational wall-clock milliseconds; never used for decisions."""

    return int(time.time() * 1000)
