import base64
import io
from zipfile import ZipFile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from watcherobot.management.publishing import catalog_wheel, sign_catalog
from watcherobot.management.releases import verify_catalog

from .test_releases import release


def test_control_wheel_contains_only_signed_data_not_release_execution():
    key = Ed25519PrivateKey.generate()
    blob = sign_catalog(
        {"schema_version": 1, "sequence": 4, "releases": [release()]},
        "release-key",
        key,
    )
    wheel = catalog_wheel("0.1.0", blob)
    with ZipFile(io.BytesIO(wheel)) as archive:
        assert archive.read("watcherobot_desktop_release/catalog.json") == blob
        assert (
            "watcherobot_desktop_release-0.1.0.dist-info/RECORD" in archive.namelist()
        )
        assert archive.read("watcherobot_desktop_release/__init__.py") == b""
    public = base64.b64encode(
        key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    assert verify_catalog(blob, {"release-key": public}).sequence == 4


def test_catalog_cli_validates_trust_before_creating_artifact(tmp_path):
    import json

    import pytest
    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    from watcherobot.management.publishing import main

    key = Ed25519PrivateKey.generate()
    private = tmp_path / "signing.pem"
    private.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    trusted = tmp_path / "trusted.json"
    trusted.write_text(
        json.dumps(
            {
                "release-key": base64.b64encode(
                    key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                ).decode()
            }
        )
    )
    payload = tmp_path / "catalog.json"
    payload.write_text(
        json.dumps({"schema_version": 1, "sequence": 1, "releases": [release()]})
    )
    arguments = [
        "--catalog",
        str(payload),
        "--signing-key",
        str(private),
        "--trusted-keys",
        str(trusted),
        "--key-id",
        "release-key",
        "--version",
        "1.0.0",
        "--output",
        str(tmp_path / "dist"),
    ]
    assert main(arguments) == 0
    output = tmp_path / "dist/watcherobot_desktop_release-1.0.0-py3-none-any.whl"
    assert output.is_file()
    with pytest.raises(FileExistsError):
        main(arguments)
    assert output.is_file()
