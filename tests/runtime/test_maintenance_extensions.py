from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import tarfile
import threading
import base64
import wave
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from PIL import Image

from watcherobot.runtime.daemon.maintenance import card_reader
from watcherobot.runtime.daemon.maintenance import service as maintenance_service
from watcherobot.runtime.daemon.maintenance.card_reader import (
    _delete_work_from_root,
    CardReaderError,
    _install_to_root,
    _install_work_to_root,
    _installed_version,
)
from watcherobot.runtime.daemon.maintenance import releases as maintenance_releases
from watcherobot.runtime.daemon.maintenance.releases import ReleaseError, _asset_is_supported, _parse_release
from watcherobot.runtime.daemon.maintenance.service import (
    MaintenanceService,
    _parse_ready_device_info,
    _serial_resource_install_plan,
)
from watcherobot.runtime.daemon.maintenance.works import (
    WorkDocumentError,
    build_portable_work_package,
    normalize_work_document,
    read_portable_work_package,
)


def test_card_reader_reports_missing_windows_volume_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(card_reader.ctypes, "windll", raising=False)

    with pytest.raises(CardReaderError, match="Windows volume APIs"):
        card_reader._windows_kernel32()


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


def test_firmware_release_list_falls_back_to_public_atom_when_github_api_is_rate_limited(monkeypatch) -> None:
    atom = b'''<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>tag:github.com,2008:Repository/1/v0.3.2</id><title>v0.3.2</title>
      <updated>2026-07-21T00:00:00Z</updated><link rel="alternate" href="https://github.com/orulink-ai/WatcheRobot_esp32/releases/tag/v0.3.2"/></entry>
    </feed>'''
    expanded = b'''
    <a href="/orulink-ai/WatcheRobot_esp32/releases/download/v0.3.2/WatcheRobot-S3-v0.3.2-esp32s3.zip">firmware</a>
    <span>sha256:36a58cc76501e3024d6f1aba6ace7b3d6b2285bfd5a59f3bdfed60f2e1999480</span>
    <span>3.48 MB</span>
    '''

    def fake_request(url: str, timeout: int = 30) -> bytes:
        del timeout
        if url.startswith(maintenance_releases.GITHUB_API):
            raise ReleaseError("HTTP Error 403: rate limit exceeded")
        if url.endswith("/releases.atom"):
            return atom
        if url.endswith("/releases/expanded_assets/v0.3.2"):
            return expanded
        raise AssertionError(url)

    maintenance_releases._release_cache.clear()
    monkeypatch.setattr(maintenance_releases, "_request_bytes", fake_request)

    releases = maintenance_releases.list_releases("firmware")

    assert releases[0]["version"] == "v0.3.2"
    assert releases[0]["assets"] == [{"name": "WatcheRobot-S3-v0.3.2-esp32s3.zip", "size": 3_480_000}]


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
    stale_author_hash = "1" * 64
    work.write_text(json.dumps({
        "name": "mine",
        "legacy_asset_reference": {"sha256": stale_author_hash},
    }), encoding="utf-8")
    stale_author_linked_asset = card / "watche" / "assets" / "anim" / f"{stale_author_hash}.animpack"
    stale_author_linked_asset.parent.mkdir(parents=True)
    stale_author_linked_asset.write_bytes(b"legacy")
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
    assert json.loads(work.read_text(encoding="utf-8"))["name"] == "mine"
    # 官方安装不读取或修改作者作品；旧的全局官方素材仍按新官方清单清理。
    assert not stale_author_linked_asset.exists()
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


def _work_document(work_id: str = "demo_work") -> dict[str, object]:
    return {
        "schema_version": 1,
        "work_id": work_id,
        "name": "演示作品",
        "duration_ms": 1200,
        "tracks": [{
            "type": "animation",
            "start_ms": 0,
            "duration_ms": 1200,
            "asset": {"source": "official", "resource_id": "happy"},
        }],
    }


def _work_document_v2(work_id: str = "demo_work") -> dict[str, object]:
    return {
        "format": "watche-user-work",
        "schema_version": 2,
        "work_id": work_id,
        "revision": 4,
        "name": "演示作品",
        "duration_ms": 1200,
        "tracks": [{
            "type": "animation",
            "start_ms": 0,
            "duration_ms": 1200,
            "asset": {"source": "official", "resource_id": "happy", "kind": "anim"},
        }],
        "creator": {
            "kind": "watcher.creator-composition",
            "version": 3,
            "workId": work_id,
            "revision": 4,
            "name": "演示作品",
            "exportedAt": "2026-08-06T00:00:00Z",
            "clips": [{
                "id": "expression-1",
                "kind": "expression",
                "resourceId": "happy",
                "label": "开心",
                "startMs": 0,
                "durationMs": 1200,
            }],
        },
    }


def _work_frames(document: dict[str, object]) -> list[str]:
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    frames = []
    for sequence, offset in enumerate(range(0, len(payload), 120)):
        encoded = base64.b64encode(payload[offset:offset + 120]).decode("ascii")
        frames.append(f"WRSD/2 WORK_DATA {sequence} {encoded}")
    frames.append(f"WRSD/2 WORK_DONE {len(payload)} {zlib.crc32(payload) & 0xffffffff:08x}")
    return frames


def test_card_reader_lists_only_sd_work_documents(monkeypatch, tmp_path: Path) -> None:
    card = tmp_path / "card"
    work_path = card / "watche" / "works" / "demo_work" / "work.json"
    work_path.parent.mkdir(parents=True)
    work_path.write_text(json.dumps(_work_document(), ensure_ascii=False), encoding="utf-8")
    (card / "watche" / "works" / "works_catalog.json").write_text('{"works":[]}', encoding="utf-8")
    monkeypatch.setattr(
        card_reader,
        "_resolve_volume",
        lambda _: card_reader.VolumeInfo("mock", card, "", "FAT32", 1, 2, 3, "v0.0.8"),
    )

    works = card_reader.list_works("mock")

    assert len(works) == 1
    assert works[0]["id"] == "demo_work"
    assert works[0]["source"] == "card_reader"
    assert works[0]["preview_expression_id"] == "happy"
    assert works[0]["track_counts"] == {"animation": 1, "action": 0, "sound": 0}


def test_format_card_reader_volume_as_fat32_returns_the_new_volume_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    card = tmp_path / "card"
    card.mkdir()
    original = card_reader.VolumeInfo("E:\\|00000001", card, "OLD", "FAT", 1, 2, 1, None)
    formatted = card_reader.VolumeInfo("E:\\|00000002", card, "", "FAT32", 3, 4, 2, None)
    calls: list[list[str]] = []

    monkeypatch.setattr(card_reader.sys, "platform", "win32")
    monkeypatch.setenv("SystemDrive", "Z:")
    monkeypatch.setattr(card_reader, "_resolve_volume", lambda _: original)
    monkeypatch.setattr(card_reader, "_inspect_volume", lambda _: formatted)
    monkeypatch.setattr(card_reader.subprocess, "run", lambda command, **_: calls.append(command))

    result = card_reader.format_volume_as_fat32(original.id)

    assert result == formatted.payload()
    assert calls and calls[0][:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert "-FileSystem FAT32" in calls[0][-1]


def test_format_card_reader_volume_does_not_reformat_existing_fat32(monkeypatch, tmp_path: Path) -> None:
    card = tmp_path / "card"
    card.mkdir()
    volume = card_reader.VolumeInfo("E:\\|00000001", card, "", "FAT32", 1, 2, 1, None)
    monkeypatch.setattr(card_reader.sys, "platform", "win32")
    monkeypatch.setenv("SystemDrive", "Z:")
    monkeypatch.setattr(card_reader, "_resolve_volume", lambda _: volume)
    monkeypatch.setattr(card_reader.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not format"))

    assert card_reader.format_volume_as_fat32(volume.id) == volume.payload()


def test_invalid_sd_directories_do_not_hide_valid_works_after_the_list_limit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    card = tmp_path / "card"
    works_root = card / "watche" / "works"
    works_root.mkdir(parents=True)
    for index in range(64):
        (works_root / f"0-invalid-{index:02d}").mkdir()
    valid = works_root / "valid_work" / "work.json"
    valid.parent.mkdir()
    valid.write_text(json.dumps(_work_document("valid_work"), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        card_reader,
        "_resolve_volume",
        lambda _: card_reader.VolumeInfo("mock", card, "", "FAT32", 1, 2, 3, "v0.0.8"),
    )

    works = card_reader.list_works("mock")

    assert [item["id"] for item in works] == ["valid_work"]


def test_serial_work_library_reassembles_chunked_documents(monkeypatch) -> None:
    catalog = {"works": [{"id": "demo_work", "name": "演示作品"}]}
    work = _work_document()

    class FakeProtocol:
        def __init__(self, port: str) -> None:
            assert port == "COM29"
            self.lines: list[str] = []

        def handshake(self, *, attempts: int, response_timeout_seconds: float) -> list[str]:
            assert attempts == maintenance_service.WORK_DISCOVERY_HANDSHAKE_ATTEMPTS
            assert response_timeout_seconds == maintenance_service.WORK_DISCOVERY_RESPONSE_TIMEOUT_SECONDS
            return ["WRSD/2", "READY", "v0.0.8", "mounted", "1", "2", "ESP_OK", "caps=work_check_v2"]

        def send(self, command: str) -> None:
            if command == "WORK_LIST":
                self.lines = _work_frames(catalog)
            elif command == "WORK_GET demo_work":
                self.lines = _work_frames(work)
            elif command == "WORK_CHECK demo_work":
                self.lines = ["WRSD/2 OK WORK_CHECK demo_work"]

        def set_read_timeout(self, timeout: float) -> None:
            assert timeout > 0

        def read_line(self) -> str:
            return self.lines.pop(0) if self.lines else ""

        def close(self) -> None:
            pass

    monkeypatch.setattr(maintenance_service, "_SerialProtocol", FakeProtocol)

    works = MaintenanceService().works(transport="serial", port="COM29")

    assert len(works) == 1
    assert works[0]["id"] == "demo_work"
    assert works[0]["source"] == "serial"
    assert works[0]["composition"]["tracks"][0]["asset"]["resource_id"] == "happy"
    assert works[0]["missing_assets"] == []


def test_serial_work_library_reports_the_missing_asset_from_firmware(monkeypatch) -> None:
    catalog = {"works": [{"id": "demo_work", "name": "演示作品"}]}

    class FakeProtocol:
        def __init__(self, port: str) -> None:
            self.lines: list[str] = []

        def handshake(self, *, attempts: int, response_timeout_seconds: float) -> list[str]:
            assert attempts == maintenance_service.WORK_DISCOVERY_HANDSHAKE_ATTEMPTS
            assert response_timeout_seconds == maintenance_service.WORK_DISCOVERY_RESPONSE_TIMEOUT_SECONDS
            return ["WRSD/2", "READY", "v0.0.8", "mounted", "1", "2", "ESP_OK", "caps=work_check_v2"]

        def send(self, command: str) -> None:
            if command == "WORK_LIST":
                self.lines = _work_frames(catalog)
            elif command == "WORK_GET demo_work":
                self.lines = _work_frames(_work_document())
            elif command == "WORK_CHECK demo_work":
                self.lines = ["WRSD/2 ERROR work_assets_missing happy/animation"]

        def set_read_timeout(self, timeout: float) -> None:
            pass

        def read_line(self) -> str:
            return self.lines.pop(0) if self.lines else ""

        def close(self) -> None:
            pass

    monkeypatch.setattr(maintenance_service, "_SerialProtocol", FakeProtocol)

    work = MaintenanceService().works(transport="serial", port="COM29")[0]

    assert work["is_valid"] is True
    assert work["missing_assets"] == ["happy/animation"]


def test_serial_work_library_rejects_overlapping_reads_before_opening_the_port(monkeypatch) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_list(_port: str) -> list[dict]:
        entered.set()
        assert release.wait(timeout=1)
        return []

    monkeypatch.setattr(maintenance_service, "_list_serial_works", blocking_list)
    service = MaintenanceService()
    worker = threading.Thread(
        target=lambda: service.works(transport="serial", port="COM29"),
        daemon=True,
    )
    worker.start()
    assert entered.wait(timeout=1)

    with pytest.raises(maintenance_service.MaintenanceError, match="正在读取"):
        service.works(transport="serial", port="COM29")

    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_card_reader_v2_work_round_trips_exact_creator_source(monkeypatch, tmp_path: Path) -> None:
    card = tmp_path / "card"
    work_path = card / "watche" / "works" / "demo_work" / "work.json"
    work_path.parent.mkdir(parents=True)
    work_path.write_text(json.dumps(_work_document_v2(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        card_reader,
        "_resolve_volume",
        lambda _: card_reader.VolumeInfo("mock", card, "", "FAT32", 1, 2, 3, "v0.0.8"),
    )

    work = card_reader.list_works("mock")[0]

    assert work["revision"] == 4
    assert work["creator"]["clips"][0]["label"] == "开心"
    assert work["creator"]["workId"] == "demo_work"


def test_work_document_rejects_obsolete_creator_composition() -> None:
    document = _work_document_v2()
    document["creator"]["version"] = 2

    with pytest.raises(WorkDocumentError):
        normalize_work_document(document, expected_id="demo_work", source="test")


def test_reader_installs_and_updates_only_one_work_without_touching_official_or_other_works(tmp_path: Path) -> None:
    card = tmp_path / "card"
    official = card / "watche" / "official" / "current" / "official_catalog.json"
    official.parent.mkdir(parents=True)
    official_payload = {
        "schema_version": 2,
        "format": "watche-official-catalog",
        "expressions": [{"id": "happy", "assets": {"animation": {"kind": "anim"}}}],
    }
    official.write_text(json.dumps(official_payload), encoding="utf-8")
    other = card / "watche" / "works" / "other_work" / "work.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps(_work_document("other_work")), encoding="utf-8")
    package = build_portable_work_package({
        "workId": "demo_work",
        "revision": 1,
        "name": "第一版",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 500}],
    })

    _install_work_to_root(package, card, lambda *_: None)
    updated = build_portable_work_package({
        "workId": "demo_work",
        "revision": 2,
        "name": "第二版",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 800}],
    })
    _install_work_to_root(updated, card, lambda *_: None)

    installed = json.loads((card / "watche" / "works" / "demo_work" / "work.json").read_text(encoding="utf-8"))
    assert installed["revision"] == 2
    assert installed["name"] == "第二版"
    assert other.is_file()
    assert json.loads(official.read_text(encoding="utf-8")) == official_payload


def test_reader_installs_bundled_work_assets_inside_only_that_work(tmp_path: Path) -> None:
    card = tmp_path / "card"
    official = card / "watche" / "official" / "current" / "official_catalog.json"
    official.parent.mkdir(parents=True)
    official.write_text(json.dumps({
        "schema_version": 2,
        "format": "watche-official-catalog",
        "expressions": [{"id": "happy", "assets": {"animation": {"kind": "anim"}}}],
    }), encoding="utf-8")
    package = build_portable_work_package({
        "workId": "action_work",
        "revision": 1,
        "name": "动作作品",
        "clips": [{
            "kind": "action",
            "resourceId": "custom-action-1",
            "startMs": 0,
            "durationMs": 500,
            "actionTracks": {
                "xDeg": [{"timeMs": 0, "angleDeg": 90}],
                "yDeg": [{"timeMs": 0, "angleDeg": 100}],
            },
        }],
    })

    _install_work_to_root(package, card, lambda *_: None)

    installed = card / "watche" / "works" / "action_work"
    action_files = list((installed / "actions").glob("*.json"))
    assert (installed / "resource_catalog.json").is_file()
    assert len(action_files) == 1
    assert json.loads(action_files[0].read_text(encoding="utf-8"))["animated_objects"]


def _data_url(mime_type: str, payload: bytes) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _gif_payload() -> bytes:
    output = io.BytesIO()
    frames = [Image.new("RGBA", (12, 8), color) for color in ((255, 0, 0, 255), (0, 255, 0, 255))]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=[80, 120], loop=0)
    return output.getvalue()


def _wav_payload() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(struct.pack("<" + "h" * 240, *([600] * 240)))
    return output.getvalue()


def test_portable_work_preserves_explicit_duration_and_creator_v3() -> None:
    package = build_portable_work_package({
        "kind": "watcher.creator-composition",
        "version": 3,
        "workId": "timed_work",
        "revision": 1,
        "name": "Timed work",
        "durationMs": 2400,
        "clips": [{
            "id": "sound-1",
            "kind": "sound",
            "resourceId": "happy",
            "label": "Happy",
            "startMs": 200,
            "durationMs": 600,
        }],
    })

    assert package.work["duration_ms"] == 2400
    assert package.work["creator"]["version"] == 3
    assert package.work["creator"]["durationMs"] == 2400


def test_portable_work_rejects_same_lane_overlap() -> None:
    with pytest.raises(WorkDocumentError, match="同一轨道.*重叠"):
        build_portable_work_package({
            "workId": "overlap_work",
            "name": "Overlap",
            "clips": [
                {"id": "sound-1", "kind": "sound", "resourceId": "happy", "label": "One", "startMs": 0, "durationMs": 500},
                {"id": "sound-2", "kind": "sound", "resourceId": "error", "label": "Two", "startMs": 400, "durationMs": 500},
            ],
        })


def test_portable_work_converts_custom_media_without_changing_the_timeline(tmp_path: Path) -> None:
    original_clips = [
        {"id": "clip-sound", "kind": "sound", "resourceId": "custom-sound-1", "label": "提示音", "startMs": 420, "durationMs": 380},
        {"id": "clip-gif", "kind": "expression", "resourceId": "custom-expression-1", "label": "自制表情", "startMs": 100, "durationMs": 900},
        {"id": "clip-action", "kind": "action", "resourceId": "custom-action-1", "label": "点头", "startMs": 250, "durationMs": 500,
         "actionTracks": {"xDeg": [{"timeMs": 0, "angleDeg": 90}, {"timeMs": 500, "angleDeg": 110}] }},
    ]
    package = build_portable_work_package({
        "workId": "media_work",
        "revision": 3,
        "name": "媒体作品",
        "exportedAt": "2026-08-06T10:00:00Z",
        "clips": original_clips,
        "assets": [
            {"id": "custom-expression-1", "kind": "expression", "name": "自制表情", "fileName": "face.gif", "mimeType": "image/gif", "dataUrl": _data_url("image/gif", _gif_payload())},
            {"id": "custom-sound-1", "kind": "sound", "name": "提示音", "fileName": "tone.wav", "mimeType": "audio/wav", "dataUrl": _data_url("audio/wav", _wav_payload())},
        ],
    })

    # Device references may change to content-addressed IDs, but the Creator
    # timeline itself is an exact round trip and is never sorted or expanded.
    assert [(clip["id"], clip["startMs"], clip["durationMs"]) for clip in package.work["creator"]["clips"]] == [
        ("clip-sound", 420, 380), ("clip-gif", 100, 900), ("clip-action", 250, 500),
    ]
    assert [(track["type"], track["start_ms"], track["duration_ms"]) for track in package.work["tracks"]] == [
        ("sound", 420, 380), ("animation", 100, 900), ("action", 250, 500),
    ]
    assert [track["clip_id"] for track in package.work["tracks"]] == [
        "clip-sound", "clip-gif", "clip-action",
    ]
    animation_path = next(path for path in package.files if path.startswith("anim/"))
    sound_path = next(path for path in package.files if path.startswith("sfx/"))
    assert struct.unpack_from("<4sHHHH", package.files[animation_path])[:5] == (b"ANPK", 2, 206, 206, 2)
    assert len(package.files[sound_path]) == 480
    assert all(asset["sourcePath"].startswith("sources/") and "dataUrl" not in asset for asset in package.work["creator"]["assets"])
    with tarfile.open(fileobj=io.BytesIO(package.serial_payload), mode="r:gz") as archive:
        assert sum(member.size for member in archive.getmembers() if member.isfile()) == package.expanded_size_bytes

    path = tmp_path / "media_work.watcher-work.zip"
    path.write_bytes(package.zip_payload)
    imported = read_portable_work_package(path)
    assert imported.work["creator"]["clips"] == package.work["creator"]["clips"]
    assert {entry["kind"] for entry in imported.work["creator"]["assets"]} == {"expression", "sound"}


def test_reader_import_restores_custom_media_and_exact_creator_timeline(monkeypatch, tmp_path: Path) -> None:
    card = tmp_path / "card"
    official = card / "watche" / "official" / "current" / "official_catalog.json"
    official.parent.mkdir(parents=True)
    official.write_text(json.dumps({"expressions": []}), encoding="utf-8")
    clips = [
        {"id": "sound-last", "kind": "sound", "resourceId": "custom-sound-1", "label": "声音", "startMs": 600, "durationMs": 200},
        {"id": "face-first", "kind": "expression", "resourceId": "custom-expression-1", "label": "表情", "startMs": 120, "durationMs": 900},
    ]
    package = build_portable_work_package({
        "workId": "reader_media",
        "revision": 2,
        "name": "读卡器媒体作品",
        "clips": clips,
        "assets": [
            {"id": "custom-expression-1", "kind": "expression", "name": "表情", "fileName": "face.gif", "mimeType": "image/gif", "dataUrl": _data_url("image/gif", _gif_payload())},
            {"id": "custom-sound-1", "kind": "sound", "name": "声音", "fileName": "tone.wav", "mimeType": "audio/wav", "dataUrl": _data_url("audio/wav", _wav_payload())},
        ],
    })
    _install_work_to_root(package, card, lambda *_: None)
    monkeypatch.setattr(
        card_reader,
        "_resolve_volume",
        lambda _: card_reader.VolumeInfo("mock", card, "", "FAT32", 1, 2, 3, "v0.0.8"),
    )

    imported = card_reader.read_work("mock", "reader_media")

    assert imported["creator"]["clips"] == clips
    assert {asset["kind"] for asset in imported["creator"]["assets"]} == {"expression", "sound"}
    assert all(asset["dataUrl"].startswith(f"data:{asset['mimeType']};base64,") for asset in imported["creator"]["assets"])


def test_reader_reports_a_corrupted_work_local_action_without_blocking_edit_import(monkeypatch, tmp_path: Path) -> None:
    card = tmp_path / "card"
    official = card / "watche" / "official" / "current" / "official_catalog.json"
    official.parent.mkdir(parents=True)
    official.write_text(json.dumps({"expressions": []}), encoding="utf-8")
    package = build_portable_work_package({
        "workId": "broken_action",
        "revision": 1,
        "name": "可修复作品",
        "clips": [{
            "kind": "action",
            "resourceId": "custom-action-broken",
            "startMs": 0,
            "durationMs": 500,
            "actionTracks": {"xDeg": [{"timeMs": 0, "angleDeg": 90}]},
        }],
    })
    _install_work_to_root(package, card, lambda *_: None)
    action_path = next((card / "watche" / "works" / "broken_action" / "actions").glob("*.json"))
    payload = action_path.read_bytes()
    action_path.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    monkeypatch.setattr(
        card_reader,
        "_resolve_volume",
        lambda _: card_reader.VolumeInfo("mock", card, "", "FAT32", 1, 2, 3, "v0.0.8"),
    )

    work = card_reader.list_works("mock")[0]

    assert work["is_valid"] is True
    assert work["creator"]["workId"] == "broken_action"
    assert work["missing_assets"] == [f"{package.work['tracks'][0]['asset']['resource_id']}/action"]


def test_reader_delete_removes_only_selected_user_work(tmp_path: Path) -> None:
    card = tmp_path / "card"
    for work_id in ("first_work", "second_work"):
        path = card / "watche" / "works" / work_id / "work.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(_work_document(work_id)), encoding="utf-8")
    official = card / "watche" / "official" / "current" / "keep.txt"
    official.parent.mkdir(parents=True)
    official.write_text("keep", encoding="utf-8")

    _delete_work_from_root(card, "first_work")

    assert not (card / "watche" / "works" / "first_work").exists()
    assert (card / "watche" / "works" / "second_work" / "work.json").is_file()
    assert official.read_text(encoding="utf-8") == "keep"


def test_reader_rejects_a_new_work_after_the_64_work_limit(tmp_path: Path) -> None:
    card = tmp_path / "card"
    official = card / "watche" / "official" / "current" / "official_catalog.json"
    official.parent.mkdir(parents=True)
    official.write_text(json.dumps({"expressions": []}), encoding="utf-8")
    works_root = card / "watche" / "works"
    for index in range(64):
        (works_root / f"work_{index:02d}").mkdir(parents=True)
    package = build_portable_work_package({
        "workId": "new_work",
        "revision": 1,
        "name": "new",
        "clips": [{
            "kind": "action",
            "resourceId": "custom-action-limit",
            "startMs": 0,
            "durationMs": 100,
            "actionTracks": {"xDeg": [{"timeMs": 0, "angleDeg": 90}]},
        }],
    })

    with pytest.raises(CardReaderError, match="64"):
        _install_work_to_root(package, card, lambda *_: None)


def test_portable_work_import_rejects_duplicate_zip_entries(tmp_path: Path) -> None:
    source = build_portable_work_package({
        "workId": "duplicate_work",
        "revision": 1,
        "name": "duplicate",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 100}],
    })
    path = tmp_path / "duplicate.watche-work.zip"
    path.write_bytes(source.zip_payload)
    with ZipFile(path, "a", compression=ZIP_DEFLATED) as archive:
        info = ZipInfo("work.json", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        archive.writestr(info, json.dumps(source.work).encode())

    with pytest.raises(WorkDocumentError, match="重复"):
        read_portable_work_package(path)


def test_portable_work_import_preserves_validated_files_in_serial_archive(tmp_path: Path) -> None:
    source = build_portable_work_package({
        "workId": "forward_work",
        "revision": 1,
        "name": "forward",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 100}],
    })
    extra_path = "sfx/" + "a" * 64 + ".pcm"
    extra_payload = b"future-bundled-sound"
    manifest = json.loads(json.dumps(source.manifest))
    manifest["files"].append({
        "path": extra_path,
        "size": len(extra_payload),
        "sha256": hashlib.sha256(extra_payload).hexdigest(),
    })
    manifest["bundled_assets"].append({"resource_id": "future", "path": extra_path})
    path = tmp_path / "forward.watche-work.zip"
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, payload in {
            **source.files,
            extra_path: extra_payload,
            "work_manifest.json": json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode(),
        }.items():
            archive.writestr(name, payload)

    imported = read_portable_work_package(path)
    with tarfile.open(fileobj=io.BytesIO(imported.serial_payload), mode="r:gz") as archive:
        assert archive.extractfile(extra_path).read() == extra_payload


def test_work_serial_archive_is_reproducible() -> None:
    composition = {
        "workId": "stable_work",
        "revision": 1,
        "name": "stable",
        "exportedAt": "2026-08-06T00:00:00Z",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 100}],
    }

    assert build_portable_work_package(composition).serial_payload == build_portable_work_package(composition).serial_payload
