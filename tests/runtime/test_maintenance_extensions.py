from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from watcherobot.runtime.daemon.maintenance import card_reader
from watcherobot.runtime.daemon.maintenance.card_reader import _install_to_root, _installed_version
from watcherobot.runtime.daemon.maintenance.releases import _asset_is_supported, _parse_release
from watcherobot.runtime.daemon.maintenance.service import (
    MaintenanceService,
    _parse_ready_device_info,
    _serial_resource_install_plan,
)


def _bundle_hash(entries: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, sha256 in sorted(entries.items()):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(sha256.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _write_sd_package(
    path: Path,
    version: str = "v0.0.8",
    animation: bytes = b"animation",
) -> None:
    animation_hash = hashlib.sha256(animation).hexdigest()
    animation_path = f"assets/anim/{animation_hash}.animpack"
    catalog = {
        "schema_version": 2,
        "format": "watche-official-catalog",
        "expressions": [{
            "id": "demo",
            "order": 0,
            "display_name": "Demo",
            "source_record_id": "record-demo",
            "loop": False,
            "assets": {
                "animation": {
                    "kind": "anim",
                    "format": "animpack-v2",
                    "sha256": animation_hash,
                    "size": len(animation),
                }
            },
        }],
    }
    fixed_states = {
        "schema_version": 1,
        "states": {
            state: "demo"
            for state in ("boot", "standby", "listening", "thinking", "speaking", "processing", "error", "upgrade")
        },
    }
    payloads = {
        "official_catalog.json": json.dumps(catalog).encode(),
        "fixed_states.json": json.dumps(fixed_states).encode(),
        animation_path: animation,
    }
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}
    manifest = {
        "schema_version": 2,
        "product": "WatcheRobot-S3",
        "layout_revision": 2,
        "bundle_version": version,
        "files": [
            {"path": name, "size": len(payloads[name]), "sha256": hashes[name]}
            for name in sorted(payloads)
        ],
        "bundle_sha256": _bundle_hash(hashes),
    }
    payloads["resource_manifest.json"] = json.dumps(manifest).encode()
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in payloads.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_ready_device_info_keeps_old_firmware_compatible() -> None:
    old = _parse_ready_device_info(["WRSD/2", "READY", "v0.0.8", "mounted", "10", "20", "ESP_OK"])
    new = _parse_ready_device_info(
        ["WRSD/2", "READY", "v0.0.8", "mounted", "10", "20", "ESP_OK", "fw=V2.4.1"]
    )

    assert old["firmware_version"] is None
    assert old["sd_version"] == "v0.0.8"
    assert new["firmware_version"] == "V2.4.1"


def test_ready_device_info_reports_deferred_replacement_capability() -> None:
    info = _parse_ready_device_info([
        "WRSD/2", "READY", "v0.0.8", "mounted", "48000000", "120000000", "ESP_OK",
        "fw=V3.1", "caps=deferred_replace",
    ])

    assert info["capabilities"] == ["deferred_replace"]
    plan = _serial_resource_install_plan(info, archive_size=6_000_000, expanded_size=60_000_000)
    assert plan.deferred_replace is True
    assert plan.required_before_upload == 6_000_000 + 4 * 1024 * 1024


def test_old_firmware_is_not_allowed_to_predelete_resources_on_tight_cards() -> None:
    info = _parse_ready_device_info([
        "WRSD/2", "READY", "v0.0.8", "mounted", "48000000", "120000000", "ESP_OK",
    ])

    try:
        _serial_resource_install_plan(info, archive_size=6_000_000, expanded_size=60_000_000)
    except Exception as error:
        assert "兼容固件" in str(error)
    else:
        raise AssertionError("旧固件在空间不足时不应提前删除官方资源")


def test_release_filter_only_exposes_burnable_assets() -> None:
    assert _asset_is_supported("firmware", "WatcheRobot-S3-v0.3.2-esp32s3.zip")
    assert not _asset_is_supported("firmware", "WatcheRobot-S3-v0.3.2-release.zip")
    assert not _asset_is_supported("firmware", "WatcheRobot-S3-v0.3.2-sdcard-anim.zip")
    assert _asset_is_supported("sd_resources", "watche-sd-resources-v0.0.8.tar.gz")

    parsed = _parse_release("firmware", {
        "tag_name": "v0.3.2",
        "name": "Firmware",
        "published_at": "2026-08-01T00:00:00Z",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": "WatcheRobot-S3-v0.3.2-esp32s3.zip", "size": 123, "browser_download_url": "https://example.test/fw.zip"},
            {"name": "WatcheRobot-S3-v0.3.2-release.zip", "size": 456, "browser_download_url": "https://example.test/all.zip"},
        ],
    })
    assert parsed is not None
    assert [asset.name for asset in parsed.assets] == ["WatcheRobot-S3-v0.3.2-esp32s3.zip"]


def test_release_job_is_additive_and_does_not_require_a_local_path(monkeypatch) -> None:
    class DeferredThread:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr("watcherobot.runtime.daemon.maintenance.service.threading.Thread", DeferredThread)
    service = MaintenanceService()

    job = service.start(
        "firmware",
        "",
        "COM29",
        release_version="v0.3.2",
        release_asset="WatcheRobot-S3-v0.3.2-esp32s3.zip",
    )

    assert job["source_type"] == "release"
    assert job["package_path"] == ""
    assert job["transport"] == "serial"


def test_card_reader_install_replaces_official_resources_and_preserves_works(tmp_path: Path) -> None:
    package = tmp_path / "resources.tar.gz"
    card = tmp_path / "card"
    card.mkdir()
    work = card / "watche" / "works" / "my-work" / "work.json"
    work.parent.mkdir(parents=True)
    work.write_text('{"name":"mine"}', encoding="utf-8")
    old = card / "watche" / "official" / "current" / "old.json"
    old.parent.mkdir(parents=True)
    old.write_text("old", encoding="utf-8")
    stale_runtime = card / "watche" / "runtime" / "old-runtime.json"
    stale_runtime.parent.mkdir(parents=True)
    stale_runtime.write_text("old", encoding="utf-8")
    stale_staging = card / "watche" / "staging" / "abandoned.part"
    stale_staging.parent.mkdir(parents=True)
    stale_staging.write_text("old", encoding="utf-8")
    stale_system = card / "watche" / "system" / "transaction.invalid.json"
    stale_system.parent.mkdir(parents=True, exist_ok=True)
    stale_system.write_text("old", encoding="utf-8")
    _write_sd_package(package)
    updates: list[tuple[str, int, str]] = []

    version = _install_to_root(package, card, lambda phase, value, line: updates.append((phase, value, line)))

    assert version == "v0.0.8"
    assert _installed_version(card) == "v0.0.8"
    assert work.read_text(encoding="utf-8") == '{"name":"mine"}'
    assert not old.exists()
    assert not stale_runtime.exists()
    assert not stale_staging.exists()
    assert not stale_system.exists()
    assert (card / "watche" / "system" / "layout.json").is_file()
    assert (card / "watche" / "system" / "accepted_official.json").is_file()
    assert any((card / "watche" / "assets" / "anim").glob("*.animpack"))
    assert updates[-1][0:2] == ("reader_completed", 99)


def test_card_reader_counts_only_missing_assets_when_checking_free_space(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = tmp_path / "resources.tar.gz"
    card = tmp_path / "card"
    card.mkdir()
    animation = b"a" * (5 * 1024 * 1024)
    animation_hash = hashlib.sha256(animation).hexdigest()
    existing = card / "watche" / "assets" / "anim" / f"{animation_hash}.animpack"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(animation)
    work = card / "watche" / "works" / "my-work" / "work.json"
    work.parent.mkdir(parents=True)
    work.write_text(json.dumps({"animation": {"sha256": animation_hash}}), encoding="utf-8")
    _write_sd_package(package, animation=animation)
    actual_usage = shutil.disk_usage(card)
    available = 5 * 1024 * 1024
    monkeypatch.setattr(
        "watcherobot.runtime.daemon.maintenance.card_reader.shutil.disk_usage",
        lambda _: shutil._ntuple_diskusage(actual_usage.total, actual_usage.total - available, available),
    )

    version = _install_to_root(package, card, lambda *_: None)

    assert version == "v0.0.8"
    assert existing.is_file()
    assert work.is_file()


def test_card_reader_recovers_interrupted_rollback_before_managed_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package = tmp_path / "resources.tar.gz"
    card = tmp_path / "card"
    card.mkdir()
    watche = card / "watche"
    rollback = watche / "official" / "rollback.reader"
    rollback.mkdir(parents=True)
    last_known_good = rollback / "last-known-good.json"
    last_known_good.write_text("old", encoding="utf-8")
    transaction = watche / "system" / "reader_transaction.json"
    transaction.parent.mkdir(parents=True)
    transaction.write_text('{"phase":"switching"}', encoding="utf-8")
    _write_sd_package(package)
    monkeypatch.setattr(
        card_reader,
        "_extract",
        lambda *_: (_ for _ in ()).throw(card_reader.CardReaderError("模拟写入中断")),
    )

    with pytest.raises(card_reader.CardReaderError, match="模拟写入中断"):
        _install_to_root(package, card, lambda *_: None)

    restored = watche / "official" / "current" / "last-known-good.json"
    assert restored.read_text(encoding="utf-8") == "old"
    assert not rollback.exists()
