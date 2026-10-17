import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.verify_sdk_management_release import verify_wheel


def wheel(tmp_path: Path, *, keys=None, include_stream=True):
    target = tmp_path / "watcherobot-0.1.13-py3-none-any.whl"
    with ZipFile(target, "w") as archive:
        for file in [
            "management/cli.py",
            "management/compatibility.py",
            "management/publishing.py",
        ]:
            archive.writestr("watcherobot/" + file, "# SDK owned")
        if include_stream:
            archive.writestr("watcherobot/management/stream.py", "def serve(): pass")
        archive.writestr(
            "watcherobot/runtime/daemon/__main__.py",
            '"--sdk-manager" "--sdk-manager-stream"',
        )
        archive.writestr(
            "watcherobot/management/release-keys.json", json.dumps(keys or {})
        )
        archive.writestr(
            "watcherobot-0.1.13.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: watcherobot\nVersion: 0.1.13\nRequires-Dist: cryptography>=44,<51\n",
        )
    return target


def test_release_requires_management_stream_and_crypto_metadata(tmp_path):
    result = verify_wheel(wheel(tmp_path))
    assert result["sdk_version"] == "0.1.13"
    assert result["update_trust_ready"] is False
    with pytest.raises(ValueError, match="stream"):
        verify_wheel(wheel(tmp_path, include_stream=False))


def test_production_guard_rejects_empty_or_invalid_trust_keys(tmp_path):
    with pytest.raises(ValueError, match="trust"):
        verify_wheel(wheel(tmp_path), require_update_trust=True)
    with pytest.raises(ValueError, match="trust"):
        verify_wheel(
            wheel(tmp_path, keys={"production": "invalid"}), require_update_trust=True
        )


def test_ota_preparation_binds_catalog_to_the_actual_sdk_wheel_and_trust(
    tmp_path, monkeypatch
):
    import base64
    import hashlib

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    from tools.prepare_sdk_ota_release import main as prepare
    from watcherobot.management.compatibility import runtime_contracts

    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    sdk = wheel(tmp_path, keys={"test": public})
    signing = tmp_path / "test-only.pem"
    signing.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    artifact = {
        "name": "watcherobot",
        "version": "0.1.13",
        "filename": sdk.name,
        "url": "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/" + sdk.name,
        "sha256": hashlib.sha256(sdk.read_bytes()).hexdigest(),
        "size": sdk.stat().st_size,
    }
    payload = {
        "schema_version": 1,
        "sequence": 1,
        "releases": [
            {
                "version": "0.1.13",
                "platform": "darwin-arm64",
                "python": ">=3.12,<3.13",
                "contracts": runtime_contracts(),
                "requirements": [artifact],
            }
        ],
    }
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(payload))
    args = [
        "--sdk-wheel",
        str(sdk),
        "--catalog",
        str(catalog),
        "--signing-key",
        str(signing),
        "--key-id",
        "test",
        "--version",
        "0.1.13",
        "--output",
        str(tmp_path / "out"),
    ]
    assert prepare(args) == 0
    assert (
        tmp_path / "out/watcherobot_desktop_release-0.1.13-py3-none-any.whl"
    ).is_file()
    from tools import publish_sdk_ota_release as publisher

    metadata_wheel = (
        tmp_path / "out/watcherobot_desktop_release-0.1.13-py3-none-any.whl"
    )
    assert (
        publisher.verify_catalog_wheel(sdk, metadata_wheel)["sdk_version"] == "0.1.13"
    )
    monkeypatch.setattr(
        publisher,
        "publish",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("verification must not publish")
        ),
    )
    assert (
        publisher.main(
            ["--sdk-wheel", str(sdk), "--catalog-wheel", str(metadata_wheel)]
        )
        == 0
    )
    artifact["sha256"] = "a" * 64
    catalog.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="SDK wheel"):
        prepare(args)


def test_publisher_context_requires_the_protected_official_workflow():
    from tools.publish_sdk_ota_release import require_publish_context

    with pytest.raises(ValueError, match="protected"):
        require_publish_context({})


def test_catalog_publication_never_regresses_version_or_replays_sequence():
    from tools.publish_sdk_ota_release import validate_publication_order

    previous = {"catalog_version": "0.1.13", "catalog_sha256": "old", "sequence": 10}
    with pytest.raises(ValueError, match="sequence"):
        validate_publication_order(
            {"catalog_version": "0.1.14", "catalog_sha256": "new", "sequence": 10},
            previous,
        )
    with pytest.raises(ValueError, match="version"):
        validate_publication_order(
            {"catalog_version": "0.1.12", "catalog_sha256": "new", "sequence": 11},
            previous,
        )
    with pytest.raises(ValueError, match="immutable"):
        validate_publication_order(
            {**previous, "catalog_sha256": "different"}, previous
        )
    validate_publication_order(previous, previous)
    validate_publication_order(
        {"catalog_version": "0.1.14", "catalog_sha256": "new", "sequence": 11}, previous
    )
