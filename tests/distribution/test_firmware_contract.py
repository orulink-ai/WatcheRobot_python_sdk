"""Safety boundaries for marketplace firmware before any device write."""

import hashlib
import json
from zipfile import ZipFile

import pytest

from watcherobot.firmware.contract import FirmwareError, FirmwareRequirement
from watcherobot.firmware.package import inspect_package


def requirement(**changes):
    return {
        "mode": "required",
        "firmware_id": "com.example.firmware",
        "version_spec": ">=1.0.0,<2.0.0",
        "recommended_version": "1.1.0",
        "tested_versions": ["1.1.0"],
        **changes,
    }


def test_identity_and_range_are_both_required():
    value = FirmwareRequirement.parse(requirement())
    assert value.accepts("com.example.firmware", "v1.2.0")
    assert not value.accepts("com.other.firmware", "1.2.0")
    assert not value.accepts("com.example.firmware", "2.0.0")
    assert value.to_dict() == requirement()


@pytest.mark.parametrize(
    "changes",
    [
        {"version_spec": ""},
        {"version_spec": "~=1.0"},
        {"version_spec": ">=1.0.0 || <2.0.0"},
        {"version_spec": "==1.*"},
        {"recommended_version": "2.0.0"},
        {"tested_versions": ["3.0.0"]},
        {"recommended_version": "1.1.0rc1"},
        {"firmware_id": "../firmware"},
        {"official": True},
        {"mode": "optional"},
    ],
)
def test_invalid_dependency_cannot_generate_plan(changes):
    with pytest.raises(FirmwareError):
        FirmwareRequirement.parse(requirement(**changes))


def test_undeclared_does_not_mean_no_requirement():
    assert FirmwareRequirement.parse(None) is None
    value = FirmwareRequirement.parse({"mode": "not_required"})
    assert value.to_dict() == {"mode": "not_required"}
    with pytest.raises(FirmwareError):
        FirmwareRequirement.parse({"mode": "not_required", "firmware_id": "hidden"})


def make_package(path, *, offset=0x20000, extra=None, wrong_hash=False):
    image = b"\xe9" + b"firmware" * 8
    segment = {
        "path": "images/app.bin",
        "offset": offset,
        "size": len(image),
        "sha256": "0" * 64 if wrong_hash else hashlib.sha256(image).hexdigest(),
    }
    manifest = {
        "schema_version": 1,
        "id": "com.example.firmware",
        "name": "Demo",
        "version": "1.0.0",
        "chip": "esp32s3",
        "skus": ["watcher"],
        "hardware_revisions": ["1"],
        "flash_size": 0x2000000,
        "segments": [segment],
    }
    with ZipFile(path, "w") as archive:
        archive.writestr("firmware.json", json.dumps(manifest))
        archive.writestr(
            "flash_args.txt",
            f"--flash_mode dio --flash_freq 80m --flash_size 32MB\n{offset:#x} images/app.bin\n",
        )
        archive.writestr("images/app.bin", image)
        if extra:
            archive.writestr(*extra)
    return manifest


def test_package_returns_validated_metadata_without_extracting(tmp_path):
    path = tmp_path / "firmware.zip"
    make_package(path)
    result = inspect_package(path)
    assert result.firmware_id == "com.example.firmware"
    assert result.version == "1.0.0"
    assert result.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.segments[0].offset == 0x20000
    assert sorted(p.name for p in tmp_path.iterdir()) == ["firmware.zip"]


@pytest.mark.parametrize(
    "extra",
    [
        ("../escape.bin", b"bad"),
        ("APP.py", b"bad"),
        ("images/APP.BIN", b"bad"),
        ("CON.bin", b"bad"),
    ],
)
def test_archive_rejects_unlisted_and_unsafe_content(tmp_path, extra):
    path = tmp_path / "firmware.zip"
    make_package(path, extra=extra)
    with pytest.raises(FirmwareError):
        inspect_package(path)


def test_package_rejects_corrupt_image_and_out_of_bounds(tmp_path):
    path = tmp_path / "firmware.zip"
    make_package(path, wrong_hash=True)
    with pytest.raises(FirmwareError):
        inspect_package(path)
    make_package(path, offset=0x2000000)
    with pytest.raises(FirmwareError):
        inspect_package(path)


def test_malformed_flash_syntax_is_a_validation_error(tmp_path):
    path = tmp_path / "firmware.zip"
    make_package(path)
    with ZipFile(path) as source:
        files = {name: source.read(name) for name in source.namelist()}
    files["flash_args.txt"] = b'--flash_mode "unterminated'
    with ZipFile(path, "w") as target:
        for name, data in files.items():
            target.writestr(name, data)
    with pytest.raises(FirmwareError):
        inspect_package(path)
