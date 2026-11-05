import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from watcherobot.firmware.contract import FirmwareError
from watcherobot.firmware.preparation import prepare_package
from tests.distribution.test_firmware_installation import table


def build(root):
    root.mkdir()
    image = b"\xe9" + bytes(11) + b"\x09\x00" + bytes(50)
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
