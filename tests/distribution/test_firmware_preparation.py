import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from watcherobot.firmware.contract import FirmwareError
from watcherobot.firmware.preparation import prepare_package
from tests.distribution.test_firmware_installation import table, valid_image


def build(root):
    root.mkdir()
    image = valid_image()
    (root / "app.bin").write_bytes(image)
    (root / "bootloader.bin").write_bytes(image)
    (root / "partition-table.bin").write_bytes(table())
    (root / "project_description.json").write_text(
        json.dumps({"project_name": "desktop_pet", "project_version": "0.3.3"})
    )
    (root / "flasher_args.json").write_text(
        json.dumps(
            {
                "extra_esptool_args": {"chip": "esp32s3"},
                "flash_settings": {
                    "flash_size": "32MB",
                    "flash_mode": "dio",
                    "flash_freq": "80m",
                },
                "flash_files": {
                    "0x0": "bootloader.bin",
                    "0x8000": "partition-table.bin",
                    "0x20000": "app.bin",
                },
            }
        )
    )
    return root


def test_prepare_chinese_build_directory_without_handwritten_addresses(tmp_path):
    result = prepare_package(build(tmp_path / "桌宠构建"), tmp_path / "cache")
    assert result["mode"] == "full"
    assert result["name"] == "desktop_pet"
    assert Path(result["package_path"]).is_file()
    with ZipFile(result["package_path"]) as z:
        m = json.loads(z.read("firmware.json"))
        assert m["schema_version"] == 2
        assert [s["offset"] for s in m["segments"]] == [0, 0x8000, 0x20000]


def test_prepare_rejects_missing_layout_before_producing_package(tmp_path):
    source = build(tmp_path / "build")
    (source / "partition-table.bin").unlink()
    with pytest.raises(FirmwareError):
        prepare_package(source, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.zip"))


def test_prepare_never_reads_build_references_outside_directory(tmp_path):
    source = build(tmp_path / "build")
    p = source / "flasher_args.json"
    value = json.loads(p.read_text())
    value["flash_files"]["0"] = "../secret.bin"
    p.write_text(json.dumps(value))
    with pytest.raises(FirmwareError):
        prepare_package(source, tmp_path / "cache")


def test_build_zip_and_prepared_zip_are_supported(tmp_path):
    source = build(tmp_path / "build")
    raw = tmp_path / "build.zip"
    with ZipFile(raw, "w") as z:
        for p in source.iterdir():
            z.write(p, p.name)
    one = prepare_package(raw, tmp_path / "cache")
    two = prepare_package(Path(one["package_path"]), tmp_path / "cache")
    assert one["sha256"] == two["sha256"]


def test_publish_rejects_legacy_missing_layout_before_any_remote_call(tmp_path):
    from watcherobot.firmware.platforms import FirmwarePlatform
    from watcherobot.distribution.ports import AccessToken
    from tests.distribution.test_firmware_contract import make_package

    p = tmp_path / "old.zip"
    make_package(p)

    def remote(_):
        raise AssertionError("must not contact provider")

    with pytest.raises(FirmwareError, match="partition"):
        FirmwarePlatform("huggingface", hf_factory=remote).publish(
            p, AccessToken("unused")
        )


def test_prepare_standard_flash_args_zip_without_marketplace_manifest(tmp_path):
    source = build(tmp_path / "build")
    (source / "flasher_args.json").unlink()
    (source / "flash_args.txt").write_text(
        "--flash_size 32MB --flash_mode dio --flash_freq 80m\n0 bootloader.bin\n0x8000 partition-table.bin\n0x20000 app.bin"
    )
    assert prepare_package(source, tmp_path / "cache")["mode"] == "full"


def test_prepare_long_unicode_source_and_cache_paths(tmp_path):
    source = tmp_path
    for _ in range(7):
        source = source / ("中文构建目录" * 5)
    import os

    if os.name == "nt":
        source = Path("\\\\?\\" + str(source))
    source.mkdir(parents=True)
    result = prepare_package(build(source / "编译结果"), source / "下载缓存")
    assert Path(result["package_path"]).is_file()


def test_normal_long_windows_path_is_normalized_before_filesystem_access(tmp_path):
    import os

    if os.name != "nt":
        pytest.skip("Windows path policy regression")
    source = tmp_path
    for _ in range(7):
        source = source / ("中文目录" * 7)
    extended = Path("\\\\?\\" + str(source))
    extended.mkdir(parents=True)
    build(extended / "build")
    result = prepare_package(source / "build", source / "缓存")
    assert Path(result["package_path"]).is_file()


def test_preparation_is_reproducible_for_retries_and_mapping_order(tmp_path):
    from unittest.mock import patch

    source = build(tmp_path / "build")
    one = prepare_package(source, tmp_path / "cache")
    args = source / "flasher_args.json"
    value = json.loads(args.read_text())
    value["flash_files"] = dict(reversed(list(value["flash_files"].items())))
    args.write_text(json.dumps(value))
    with patch("zipfile.time.localtime", return_value=(2001, 1, 1, 0, 0, 0, 0, 1, -1)):
        two = prepare_package(source, tmp_path / "cache")
    assert one["sha256"] == two["sha256"]


def test_system_symlink_ancestors_do_not_reject_legitimate_builds(
    tmp_path, monkeypatch
):
    from watcherobot.firmware.paths import filesystem_path

    source = build(tmp_path / "build")
    outside = filesystem_path(tmp_path.parent)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == outside or original(p))
    assert prepare_package(source, tmp_path / "cache")["mode"] == "full"


def test_symlink_inside_selected_build_is_still_rejected(tmp_path, monkeypatch):
    from watcherobot.firmware.paths import filesystem_path

    source = build(tmp_path / "build")
    unsafe = filesystem_path(source / "app.bin")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == unsafe or original(p))
    with pytest.raises(FirmwareError, match="symbolic"):
        prepare_package(source, tmp_path / "cache")


@pytest.mark.parametrize("provider", ["huggingface", "gitee"])
@pytest.mark.parametrize("damage", ["missing_layout", "corrupt_image"])
def test_both_upload_providers_reject_bad_packages_before_auth_or_remote_io(
    tmp_path, provider, damage
):
    import httpx
    from watcherobot.firmware.platforms import FirmwarePlatform
    from watcherobot.distribution.ports import AccessToken
    from tests.distribution.test_firmware_contract import make_package
    from tests.distribution.test_firmware_installation import package, valid_image

    path = tmp_path / "bad.zip"
    if damage == "missing_layout":
        make_package(path)
    else:
        data = bytearray(valid_image())
        data[-1] ^= 1
        package(path, image=bytes(data))

    def remote(*a, **kw):
        raise AssertionError("Invalid package must not contact either provider")

    with httpx.Client(transport=httpx.MockTransport(remote)) as client:
        with pytest.raises(FirmwareError):
            FirmwarePlatform(provider, client=client, hf_factory=remote).publish(
                path, AccessToken("unused")
            )


@pytest.mark.parametrize("use_json_flash_args", [True, False])
def test_build_zip_with_display_metadata_is_prepared_like_directory(
    tmp_path, use_json_flash_args
):
    source = build(tmp_path / "custom-build")
    if not use_json_flash_args:
        (source / "flasher_args.json").unlink()
    (source / "flash_args.txt").write_text(
        "--flash_size 32MB --flash_mode dio --flash_freq 80m\n"
        "0 bootloader.bin\n0x8000 partition-table.bin\n0x20000 app.bin"
    )
    (source / "firmware.json").write_text(
        json.dumps({"id": "org.example.custom", "name": "Custom Firmware"})
    )
    expected = prepare_package(source, tmp_path / "directory-cache")
    raw = tmp_path / "custom-build.zip"
    with ZipFile(raw, "w") as archive:
        for path in source.iterdir():
            archive.write(path, path.name)
    actual = prepare_package(raw, tmp_path / "zip-cache")
    assert actual["mode"] == "full"
    assert actual["name"] == "Custom Firmware"
    assert actual["firmware_id"] == "org.example.custom"
    assert actual["sha256"] == expected["sha256"]


def test_malformed_package_manifest_is_not_reinterpreted_as_build_metadata(tmp_path):
    from tests.distribution.test_firmware_installation import package

    original = package(tmp_path / "original.zip")
    malformed = tmp_path / "malformed.zip"
    with ZipFile(original) as source, ZipFile(malformed, "w") as output:
        for item in source.infolist():
            data = source.read(item)
            if item.filename == "firmware.json":
                manifest = json.loads(data)
                del manifest["name"]
                data = json.dumps(manifest).encode("utf-8")
            output.writestr(item, data)
    with pytest.raises(FirmwareError, match="Invalid firmware manifest fields"):
        prepare_package(malformed, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.zip"))


@pytest.mark.parametrize(
    "field", ["extra_esptool_args", "flash_settings", "flash_files"]
)
def test_build_rejects_non_object_flash_metadata_with_field_name(tmp_path, field):
    source = build(tmp_path / "invalid-metadata")
    metadata = source / "flasher_args.json"
    value = json.loads(metadata.read_text())
    value[field] = []
    metadata.write_text(json.dumps(value))
    with pytest.raises(FirmwareError, match=field):
        prepare_package(source, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.zip"))


@pytest.mark.parametrize("explicit_id", [None, "org.example.desktop-pet"])
def test_unicode_project_name_keeps_display_name_and_prepares_stable_identity(
    tmp_path, explicit_id
):
    source = build(tmp_path / "中文项目")
    (source / "project_description.json").write_text(
        json.dumps({"project_name": "桌宠副屏", "project_version": "0.3.3"}),
        encoding="utf-8",
    )
    if explicit_id:
        (source / "firmware.json").write_text(
            json.dumps({"id": explicit_id}), encoding="utf-8"
        )
    expected = prepare_package(source, tmp_path / "cache")
    assert expected["name"] == "桌宠副屏"
    if explicit_id:
        assert expected["firmware_id"] == explicit_id
    else:
        assert expected["firmware_id"].startswith("local.project-")
    raw = tmp_path / "中文构建.zip"
    with ZipFile(raw, "w") as archive:
        for path in source.iterdir():
            archive.write(path, path.name)
    actual = prepare_package(raw, tmp_path / "zip-cache")
    assert actual["firmware_id"] == expected["firmware_id"]
    assert actual["sha256"] == expected["sha256"]
    if not explicit_id:
        (source / "project_description.json").write_text(
            json.dumps({"project_name": "桌宠相框", "project_version": "0.3.3"}),
            encoding="utf-8",
        )
        other = prepare_package(source, tmp_path / "cache")
        assert other["firmware_id"] != expected["firmware_id"]


@pytest.mark.parametrize("use_json_flash_args", [True, False])
def test_wrapped_release_build_zip_prepares_without_manually_extracting(
    tmp_path, use_json_flash_args
):
    source = build(tmp_path / "build")
    if not use_json_flash_args:
        (source / "flasher_args.json").unlink()
        (source / "flash_args.txt").write_text(
            "--flash_size 32MB --flash_mode dio --flash_freq 80m\n"
            "0 bootloader.bin\n0x8000 partition-table.bin\n0x20000 app.bin",
            encoding="utf-8",
        )
    expected = prepare_package(source, tmp_path / "directory-cache")
    raw = tmp_path / "官方完整固件.zip"
    with ZipFile(raw, "w") as archive:
        for path in source.iterdir():
            archive.write(path, "官方固件发布目录/" + path.name)
    actual = prepare_package(raw, tmp_path / "zip-cache")
    assert actual["sha256"] == expected["sha256"]
    assert actual["mode"] == "full"


def test_ambiguous_build_roots_are_rejected(tmp_path):
    source = build(tmp_path / "build")
    raw = tmp_path / "ambiguous.zip"
    with ZipFile(raw, "w") as archive:
        for folder in ("release-a", "release-b"):
            for path in source.iterdir():
                archive.write(path, folder + "/" + path.name)
    with pytest.raises(FirmwareError, match="exactly one build directory"):
        prepare_package(raw, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.zip"))


def test_wrapped_package_markers_are_not_rebuilt_as_display_metadata(tmp_path):
    source = build(tmp_path / "build")
    (source / "firmware.json").write_text(
        json.dumps({"schema_version": 2, "name": "Broken package"}), encoding="utf-8"
    )
    raw = tmp_path / "wrapped-broken-package.zip"
    with ZipFile(raw, "w") as archive:
        for path in source.iterdir():
            archive.write(path, "release/" + path.name)
    with pytest.raises(FirmwareError, match="package metadata"):
        prepare_package(raw, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.zip"))


def test_root_package_marker_without_flash_args_cannot_fall_back_to_nested_build(
    tmp_path,
):
    source = build(tmp_path / "build")
    raw = tmp_path / "broken-package-with-build.zip"
    with ZipFile(raw, "w") as archive:
        archive.writestr(
            "firmware.json", json.dumps({"schema_version": 2, "segments": []})
        )
        for path in source.iterdir():
            archive.write(path, "release/" + path.name)
    with pytest.raises(FirmwareError, match="Missing or oversized firmware metadata"):
        prepare_package(raw, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.zip"))


def test_nested_bootloader_metadata_does_not_make_outer_build_ambiguous(tmp_path):
    source = build(tmp_path / "build")
    expected = prepare_package(source, tmp_path / "directory-cache")
    raw = tmp_path / "idf-build.zip"
    with ZipFile(raw, "w") as archive:
        for path in source.iterdir():
            archive.write(path, "release/" + path.name)
        archive.writestr("release/bootloader/flasher_args.json", "{}")
    actual = prepare_package(raw, tmp_path / "zip-cache")
    assert actual["sha256"] == expected["sha256"]


@pytest.mark.parametrize("marker", ["schema_version", "segments"])
def test_directory_package_markers_cannot_be_rebuilt_as_display_metadata(
    tmp_path, marker
):
    source = build(tmp_path / "build")
    (source / "firmware.json").write_text(
        json.dumps(
            {
                marker: 999 if marker == "schema_version" else [],
                "name": "Broken package",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FirmwareError, match="package"):
        prepare_package(source, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.zip"))


def test_growing_build_image_is_bounded_by_actual_bytes(tmp_path, monkeypatch):
    import io
    from watcherobot.firmware import preparation
    from watcherobot.firmware.paths import filesystem_path

    source = build(tmp_path / "build")
    image = filesystem_path(source / "app.bin")
    grown = image.read_bytes() + bytes(2048)
    original = Path.open
    monkeypatch.setattr(preparation, "MAX_EXPANDED_BYTES", 1024)

    def opened(path, mode="r", *args, **kwargs):
        if path == image and mode == "rb":
            return io.BytesIO(grown)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(FirmwareError, match="expanded limit"):
        prepare_package(source, tmp_path / "cache")
    assert not list((tmp_path / "cache").glob("*.zip"))


def test_growing_build_metadata_is_bounded_before_json_decode(tmp_path, monkeypatch):
    import io
    from watcherobot.firmware.paths import filesystem_path

    source = build(tmp_path / "build")
    metadata = filesystem_path(source / "project_description.json")
    grown = json.dumps({"project_name": "x" * 66000, "project_version": "0.3.3"})
    original = Path.open

    def opened(path, mode="r", *args, **kwargs):
        if path == metadata:
            return io.BytesIO(grown.encode()) if mode == "rb" else io.StringIO(grown)
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(FirmwareError, match="metadata exceeds size limit"):
        prepare_package(source, tmp_path / "cache")
