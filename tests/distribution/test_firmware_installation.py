import hashlib
import json
import struct
from zipfile import ZipFile

import pytest

from watcherobot.firmware.contract import FirmwareError
from watcherobot.firmware.installation import (
    inspect_installation,
    parse_partitions,
    plan_installation,
)
from tests.distribution.test_firmware_contract import make_package


def table(offset=0x20000, size=0x600000):
    return (
        struct.pack("<HBBII16sI", 0x50AA, 0, 0, offset, size, b"app", 0) + b"\xff" * 32
    )


def valid_image():
    header = bytearray(24)
    header[0] = 0xE9
    header[1] = 1
    header[12] = 9
    header[23] = 1
    payload = b"\x01\x02\x03\x04"
    data = bytes(header) + struct.pack("<II", 0x3FC88000, len(payload)) + payload
    checksum = 0xEF
    for value in payload:
        checksum ^= value
    data += bytes((15 - len(data) % 16) % 16) + bytes([checksum])
    return data + hashlib.sha256(data).digest()


def package(
    path, *, offset=0x20000, full=False, missing_boot=False, image=None, layout=None
):
    image = valid_image() if image is None else image
    files = {
        "application.bin": image,
        "partition-table.bin": table(offset) if layout is None else layout,
    }
    segments = [(offset, "application.bin")]
    if full:
        segments.insert(0, (0x8000, "partition-table.bin"))
        if not missing_boot:
            files["bootloader.bin"] = image
            segments.insert(0, (0, "bootloader.bin"))
    manifest = {
        "schema_version": 2,
        "id": "com.example.firmware",
        "name": "Demo",
        "version": "1.0.0",
        "chip": "esp32s3",
        "flash_size": 0x2000000,
        "skus": ["watcher"],
        "hardware_revisions": ["1"],
        "partition_table": "partition-table.bin",
        "segments": [
            {
                "offset": o,
                "path": n,
                "size": len(files[n]),
                "sha256": hashlib.sha256(files[n]).hexdigest(),
            }
            for o, n in segments
        ],
    }
    with ZipFile(path, "w") as z:
        z.writestr("firmware.json", json.dumps(manifest))
        z.writestr(
            "flash_args.txt",
            "--flash_size 32MB\n" + "\n".join(f"{o:#x} {n}" for o, n in segments),
        )
        for n, b in files.items():
            z.writestr(n, b)
    return path


def test_reference_partition_table_is_not_a_write_segment(tmp_path):
    info = inspect_installation(package(tmp_path / "app.zip"))
    assert info.mode == "application"
    assert len(info.package.segments) == 1
    assert info.partitions[0].offset == 0x20000


def test_legacy_app_without_layout_is_readable_but_not_publishable(tmp_path):
    p = tmp_path / "legacy.zip"
    make_package(p)
    assert inspect_installation(p).mode == "unverified"
    with pytest.raises(FirmwareError, match="partition"):
        inspect_installation(p, require_layout=True)


def test_full_install_needs_bootloader(tmp_path):
    with pytest.raises(FirmwareError, match="bootloader"):
        inspect_installation(
            package(tmp_path / "bad.zip", full=True, missing_boot=True)
        )


def test_device_mismatch_cannot_relocate_application(tmp_path):
    info = inspect_installation(package(tmp_path / "app.zip", offset=0x10000))
    with pytest.raises(FirmwareError, match="layout"):
        plan_installation(info, table(), 0x2000000)


def test_full_migration_requires_explicit_confirmation(tmp_path):
    info = inspect_installation(
        package(tmp_path / "full.zip", offset=0x10000, full=True)
    )
    plan = plan_installation(info, table(), 0x2000000)
    assert plan.requires_confirmation is True
    assert plan.mode == "full"
    assert (
        plan_installation(info, table(0x10000), 0x2000000).requires_confirmation is True
    )


def test_matching_app_preserves_layout(tmp_path):
    plan = plan_installation(
        inspect_installation(package(tmp_path / "app.zip")), table(), 0x2000000
    )
    assert plan.mode == "application"
    assert not plan.requires_confirmation


@pytest.mark.parametrize(
    "data", [b"broken", table(size=0), table() + table(), table(offset=0x21000)]
)
def test_malformed_partition_table_is_rejected(data):
    with pytest.raises(FirmwareError):
        parse_partitions(data, 0x2000000)


def test_partition_table_checksum_is_checked():
    entry = table()[:32]
    assert parse_partitions(
        entry + b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(entry).digest(), 0x2000000
    )
    with pytest.raises(FirmwareError):
        parse_partitions(entry + b"\xeb\xeb" + b"\xff" * 14 + bytes(16), 0x2000000)


def release_artifact(tmp_path):
    from watcherobot.firmware.catalog import FirmwareRelease

    path = package(tmp_path / "source.zip")
    data = path.read_bytes()
    return (
        FirmwareRelease(
            "huggingface",
            "com.example.firmware",
            "1.0.0",
            "author/fw",
            "a" * 40,
            len(data),
            hashlib.sha256(data).hexdigest(),
            False,
            "versions/1.0.0/firmware.zip",
        ),
        data,
    )


def test_custom_data_partition_types_and_nondefault_table_layout_are_supported():
    data = (
        struct.pack("<HBBII16sI", 0x50AA, 0x40, 1, 0x4000, 0x1000, b"custom", 0)
        + b"\xff" * 32
    )
    assert parse_partitions(data, 0x2000000)[0].type == 0x40


@pytest.mark.parametrize(
    "damage", ["truncated", "segment_length", "checksum", "digest"]
)
def test_corrupt_images_cannot_pass_even_with_valid_package_hashes(tmp_path, damage):
    image = bytearray(valid_image())
    if damage == "truncated":
        image = image[:24]
    elif damage == "segment_length":
        struct.pack_into("<I", image, 28, 0x400000)
    elif damage == "checksum":
        image[47] ^= 1
    else:
        image[-1] ^= 1
    with pytest.raises(FirmwareError, match="image"):
        inspect_installation(
            package(tmp_path / "bad-image.zip", image=bytes(image)), require_layout=True
        )


def test_esptool_generated_image_is_accepted(tmp_path):
    from esptool.bin_image import ESP32S3FirmwareImage, ImageSegment

    image = ESP32S3FirmwareImage()
    image.entrypoint = 0x40370000
    image.segments = [ImageSegment(0x3FC88000, b"\x01\x02\x03\x04")]
    image.segments[0].name = ".dram0.data"
    data = image.save(None)
    assert isinstance(data, bytes)
    assert (
        inspect_installation(package(tmp_path / "esptool.zip", image=data)).mode
        == "application"
    )


# These are binary ESP-IDF partition types/subtypes, independent of labels.
UNSUPPORTED_APPLICATION_LAYOUTS = [
    [(0, 0x10, 0x20000, "ota_0"), (0, 0x11, 0x120000, "ota_1")],
    [(0, 0x11, 0x20000, "ota_1"), (0, 0x10, 0x120000, "ota_0")],
    [(0, 0, 0x20000, "factory"), (0, 0x10, 0x120000, "ota_0")],
    [(0, 0, 0x20000, "factory"), (1, 0, 0x9000, "selection")],
    [(0, 0x1F, 0x20000, "custom_label")],
    [(0, 0x20, 0x20000, "test")],
    [(0, 0, 0x20000, "factory"), (0, 0, 0x120000, "second_factory")],
]


def encoded_layout(entries):
    return b"".join(
        struct.pack(
            "<HBBII16sI",
            0x50AA,
            kind,
            subtype,
            offset,
            0x100000 if kind == 0 else 0x2000,
            name.encode(),
            0,
        )
        for kind, subtype, offset, name in entries
    ) + b"\xff" * 32


@pytest.mark.parametrize("entries", UNSUPPORTED_APPLICATION_LAYOUTS)
def test_application_package_rejects_ambiguous_boot_selection(tmp_path, entries):
    path = package(tmp_path / "unsafe-update.zip", layout=encoded_layout(entries))
    with pytest.raises(FirmwareError, match="single factory.*OTA"):
        inspect_installation(path, require_layout=True)


@pytest.mark.parametrize("entries", UNSUPPORTED_APPLICATION_LAYOUTS)
def test_application_plan_cannot_accept_unsupported_boot_selection(tmp_path, entries):
    from dataclasses import replace

    # A caller can hold Installation data parsed before this policy was added.
    info = inspect_installation(package(tmp_path / "factory-update.zip"))
    layout = encoded_layout(entries)
    info = replace(info, partitions=parse_partitions(layout, 0x2000000))
    with pytest.raises(FirmwareError, match="single factory.*OTA"):
        plan_installation(info, layout, 0x2000000)


def test_single_factory_update_allows_non_ota_data_partitions(tmp_path):
    layout = encoded_layout(
        [
            (0, 0, 0x20000, "custom_app_name"),
            (1, 2, 0x9000, "nvs"),
            (0x40, 0, 0xB000, "custom_data"),
        ]
    )
    info = inspect_installation(package(tmp_path / "factory.zip", layout=layout))
    plan = plan_installation(info, layout, 0x2000000)
    assert plan.mode == "application"
    assert plan.requires_confirmation is False
