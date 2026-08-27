from __future__ import annotations

import json
from pathlib import Path

from watcherobot.runtime.daemon.pairing.bindings_store import (
    DeviceBinding,
    DeviceBindingsStore,
    DeviceBindingsSnapshot,
)

HEX32 = "a" * 32
SECRET = "b" * 64


def make_binding(**overrides) -> DeviceBinding:
    values = {
        "binding_secret": SECRET,
        "target_mode": "desktop_link",
        "last_peer_ip": "192.168.1.23",
        "last_ws_port": 8765,
        "paired_at_ms": 1000,
        "last_connected_at_ms": 2000,
    }
    values.update(overrides)
    return DeviceBinding(**values)


def test_load_creates_identity_file_on_first_use(tmp_path: Path) -> None:
    store = DeviceBindingsStore(tmp_path)

    snapshot = store.load()

    assert snapshot.device is None
    assert len(snapshot.daemon_instance_id) == 32
    raw = json.loads((tmp_path / "device-bindings.json").read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["daemon_instance_id"] == snapshot.daemon_instance_id
    assert raw["device"] is None


def test_load_is_idempotent_across_restarts(tmp_path: Path) -> None:
    store = DeviceBindingsStore(tmp_path)
    first = store.load()
    # Simulate a later process reusing the same state root.
    second = DeviceBindingsStore(tmp_path).load()

    assert second == first


def test_write_and_read_roundtrip_with_device(tmp_path: Path) -> None:
    store = DeviceBindingsStore(tmp_path)
    identity = store.load()
    store.write(
        DeviceBindingsSnapshot(
            daemon_instance_id=identity.daemon_instance_id,
            device=make_binding(),
        )
    )

    reloaded = DeviceBindingsStore(tmp_path).load()

    assert reloaded.device == make_binding()


def test_corrupt_file_regenerates_fresh_state(tmp_path: Path) -> None:
    (tmp_path / "device-bindings.json").write_text("{not json", encoding="utf-8")
    store = DeviceBindingsStore(tmp_path)

    snapshot = store.load()

    assert snapshot.device is None
    raw = json.loads((tmp_path / "device-bindings.json").read_text(encoding="utf-8"))
    assert raw["daemon_instance_id"] == snapshot.daemon_instance_id


def test_invalid_version_or_identity_is_treated_as_missing(tmp_path: Path) -> None:
    store = DeviceBindingsStore(tmp_path)

    store.path.write_text(
        json.dumps({"version": 2, "daemon_instance_id": HEX32, "device": None}),
        encoding="utf-8",
    )
    assert store.load().daemon_instance_id != HEX32

    store.path.write_text(
        json.dumps({"version": 1, "daemon_instance_id": "XYZ", "device": None}),
        encoding="utf-8",
    )
    assert len(store.load().daemon_instance_id) == 32


def test_device_entry_with_bad_secret_is_dropped_but_identity_kept(
    tmp_path: Path,
) -> None:
    store = DeviceBindingsStore(tmp_path)
    identity = store.load()
    store.path.write_text(
        json.dumps(
            {
                "version": 1,
                "daemon_instance_id": identity.daemon_instance_id,
                "device": {"binding_secret": "nope"},
            }
        ),
        encoding="utf-8",
    )

    snapshot = store.load()

    assert snapshot.daemon_instance_id == identity.daemon_instance_id
    assert snapshot.device is None


def test_clear_device_forgets_only_the_device(tmp_path: Path) -> None:
    store = DeviceBindingsStore(tmp_path)
    identity = store.load()
    store.write(DeviceBindingsSnapshot(identity.daemon_instance_id, make_binding()))

    cleared = store.clear_device()
    after = DeviceBindingsStore(tmp_path).load()

    assert cleared is True
    assert after.daemon_instance_id == identity.daemon_instance_id
    assert after.device is None

    assert store.clear_device() is False


def test_write_rejects_non_hex_credentials(tmp_path: Path) -> None:
    store = DeviceBindingsStore(tmp_path)
    identity = store.load()

    try:
        store.write(
            DeviceBindingsSnapshot(identity.daemon_instance_id, make_binding(binding_secret="short"))
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for invalid binding secret")

    try:
        store.write(DeviceBindingsSnapshot("uppercase!", None))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for invalid instance id")


def test_no_temp_file_left_behind(tmp_path: Path) -> None:
    store = DeviceBindingsStore(tmp_path)
    store.load()
    store.write(DeviceBindingsStore(tmp_path).load())

    assert list(tmp_path.glob("*.tmp")) == []
