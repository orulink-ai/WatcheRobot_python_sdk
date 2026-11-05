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
    path, *, offset=0x20000, full=False, missing_boot=False, image=None, layout=None,
    table_offset=0x8000,
):
    image = valid_image() if image is None else image
    files = {
        "application.bin": image,
        "partition-table.bin": table(offset) if layout is None else layout,
    }
    segments = [(offset, "application.bin")]
    if full:
        segments.insert(0, (table_offset, "partition-table.bin"))
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
    with pytest.raises(FirmwareError, match="bootloader image at address zero"):
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
DECLARED_APPLICATION_LAYOUTS = [
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


@pytest.mark.parametrize("entries", DECLARED_APPLICATION_LAYOUTS)
def test_application_package_preserves_declared_target_without_boot_slot_selection(
    tmp_path, entries
):
    path = package(tmp_path / "declared-update.zip", layout=encoded_layout(entries))
    original = path.read_bytes()
    info = inspect_installation(path, require_layout=True)
    assert info.mode == "application"
    assert [(s.offset, s.path) for s in info.package.segments] == [
        (0x20000, "application.bin")
    ]
    assert info.partitions == parse_partitions(encoded_layout(entries), 0x2000000)
    assert path.read_bytes() == original


@pytest.mark.parametrize("entries", DECLARED_APPLICATION_LAYOUTS)
def test_application_plan_accepts_declared_slot_without_changing_ota_selection(
    tmp_path, entries
):
    from dataclasses import replace

    # The planner also accepts an already parsed Installation from its caller.
    info = inspect_installation(package(tmp_path / "factory-update.zip"))
    layout = encoded_layout(entries)
    info = replace(info, partitions=parse_partitions(layout, 0x2000000))
    segments = info.package.segments
    plan = plan_installation(info, layout, 0x2000000)
    assert plan.mode == "application"
    assert plan.requires_confirmation is False
    assert plan.device_layout_sha256 == hashlib.sha256(layout).hexdigest()
    assert info.package.segments == segments
    assert [(s.offset, s.path) for s in info.package.segments] == [
        (0x20000, "application.bin")
    ]


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


@pytest.mark.parametrize("segment_kind", ["data", "bootloader", "table"])
def test_application_mode_still_rejects_non_application_write_segments(
    tmp_path, segment_kind
):
    layout = encoded_layout(
        [(0, 0x10, 0x20000, "ota_0"), (1, 0, 0x9000, "selection")]
    )
    original = package(tmp_path / "source.zip", layout=layout)
    with ZipFile(original) as archive:
        files = {item.filename: archive.read(item) for item in archive.infolist()}
    name, offset, data, error = {
        "data": ("selection.bin", 0x9000, bytes(32), "data partitions"),
        "bootloader": ("bootloader.bin", 0, valid_image(), "Incomplete application"),
        "table": ("partition-table.bin", 0x8000, layout, "bootloader"),
    }[segment_kind]
    manifest = json.loads(files["firmware.json"])
    manifest["segments"].append({
        "path": name, "offset": offset, "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    })
    files["firmware.json"] = json.dumps(manifest).encode()
    files["flash_args.txt"] += f"\n{offset:#x} {name}".encode()
    files[name] = data
    path = tmp_path / "extra-segment.zip"
    with ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with pytest.raises(FirmwareError, match=error):
        inspect_installation(path, require_layout=True)


def test_full_package_preserves_author_declared_custom_table_address(tmp_path):
    info = inspect_installation(
        package(tmp_path / "custom-table.zip", full=True, table_offset=0x9000),
        require_layout=True,
    )
    assert info.mode == "full"
    assert info.table_offset == 0x9000
    assert [(s.offset, s.path) for s in info.package.segments] == [
        (0, "bootloader.bin"),
        (0x9000, "partition-table.bin"),
        (0x20000, "application.bin"),
    ]
