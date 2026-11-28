from __future__ import annotations

import base64
import hashlib
import io
import json
import struct
import tarfile
import threading
import wave
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from PIL import Image

from watcherobot.runtime.daemon.maintenance import releases as maintenance_releases
from watcherobot.runtime.daemon.maintenance import service as maintenance_service
from watcherobot.runtime.daemon.maintenance.releases import ReleaseError, _asset_is_supported, _parse_release
from watcherobot.runtime.daemon.maintenance.service import (
    MaintenanceService,
    _parse_ready_device_info,
)
from watcherobot.runtime.daemon.maintenance.works import (
    WorkDocumentError,
    build_portable_work_package,
    normalize_work_document,
    read_portable_work_package,
    validate_package_dependencies,
)


def test_ready_device_info_keeps_old_firmware_compatible() -> None:
    old = _parse_ready_device_info(["WRSD/2", "READY", "v0.0.8", "mounted", "10", "20", "ESP_OK"])
    new = _parse_ready_device_info(
        ["WRSD/2", "READY", "v0.0.8", "mounted", "10", "20", "ESP_OK", "fw=V2.4.1"]
    )

    assert old["firmware_version"] is None
    assert old["sd_version"] == "v0.0.8"
    assert new["firmware_version"] == "V2.4.1"


def test_release_filter_only_exposes_burnable_firmware_assets() -> None:
    assert _asset_is_supported("firmware", "WatcheRobot-S3-v0.3.2-esp32s3.zip")
    assert not _asset_is_supported("firmware", "WatcheRobot-S3-v0.3.2-release.zip")
    assert not _asset_is_supported("firmware", "WatcheRobot-S3-v0.3.2-sdcard-anim.zip")
    assert not _asset_is_supported("sd_resources", "watche-sd-resources-v0.1.0.tar.gz")

    with pytest.raises(ReleaseError, match="不支持的发布资源类型"):
        maintenance_releases.list_releases("sd_resources")

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


def test_validate_package_dependencies_accepts_v3_root_catalog() -> None:
    package = build_portable_work_package({
        "workId": "v3_dep_work",
        "revision": 1,
        "name": "v3 依赖测试",
        "clips": [{"kind": "expression", "resourceId": "happy", "startMs": 0, "durationMs": 500}],
    })
    catalog_v3 = {
        "schema_version": 3,
        "format": "watche-official-catalog",
        "expressions": [{
            "id": "happy",
            "assets": {
                "animation": {
                    "kind": "anim",
                    "path": "anim/happy.animpack",
                    "format": "animpack-v3",
                    "sha256": "0" * 64,
                    "size": 1024,
                },
            },
        }],
    }

    validate_package_dependencies(package, catalog_v3)

    with pytest.raises(WorkDocumentError, match="catalog.json"):
        validate_package_dependencies(package, {"invalid": True})


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


def test_work_document_rejects_obsolete_creator_composition() -> None:
    document = _work_document_v2()
    document["creator"]["version"] = 2

    with pytest.raises(WorkDocumentError):
        normalize_work_document(document, expected_id="demo_work", source="test")


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
