from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from watcherobot.management.releases import (
    recommended_release,
    validate_artifact_url,
    verify_catalog,
)


def signed(payload: dict) -> tuple[bytes, dict[str, str]]:
    key = Ed25519PrivateKey.generate()
    raw = json.dumps(payload).encode()
    return json.dumps(
        {
            "key_id": "test",
            "payload": base64.b64encode(raw).decode(),
            "signature": base64.b64encode(key.sign(raw)).decode(),
        }
    ).encode(), {
        "test": base64.b64encode(
            key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode()
    }


def release(version="0.1.13", management=1):
    return {
        "version": version,
        "platform": "darwin-arm64",
        "python": ">=3.10,<3.13",
        "contracts": {
            "manager": 1,
            "management": management,
            "distribution": 1,
            "application": 1,
            "state": 1,
        },
        "requirements": [
            {
                "name": "watcherobot",
                "version": version,
                "filename": f"watcherobot-{version}-py3-none-any.whl",
                "url": f"https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/{version}.whl",
                "sha256": "a" * 64,
                "size": 100,
            }
        ],
        "notes": "Fixes",
    }


def test_signature_is_required_and_tampering_rejected():
    blob, keys = signed({"schema_version": 1, "sequence": 3, "releases": [release()]})
    assert verify_catalog(blob, keys).sequence == 3
    envelope = json.loads(blob)
    envelope["payload"] = base64.b64encode(b"{}").decode()
    with pytest.raises(ValueError):
        verify_catalog(json.dumps(envelope).encode(), keys)
    with pytest.raises(ValueError):
        verify_catalog(blob, {})


def test_latest_compatible_stable_release_is_recommended():
    blob, keys = signed(
        {
            "schema_version": 1,
            "sequence": 3,
            "releases": [
                release("0.1.12"),
                release("0.1.13"),
                release("0.1.14", 2),
                release("0.1.15rc1"),
            ],
        }
    )
    catalog = verify_catalog(blob, keys)
    req = {"manager": ">=1,<2", "management": ">=1,<2", "distribution": ">=1,<2"}
    assert (
        recommended_release(catalog, req, "darwin-arm64", "3.12.13").version == "0.1.13"
    )
    assert recommended_release(catalog, req, "win32-x64", "3.12.13") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://mirrors.tuna.tsinghua.edu.cn/pypi/web/a.whl",
        "https://github.com/a.whl",
        "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/../evil",
        "https://u:p@mirrors.tuna.tsinghua.edu.cn/pypi/web/a.whl",
    ],
)
def test_download_cannot_escape_mirror(url):
    with pytest.raises(ValueError):
        validate_artifact_url(url)


def test_catalog_requires_sdk_pin_and_complete_contracts():
    item = release()
    item["requirements"] = []
    blob, keys = signed({"schema_version": 1, "sequence": 3, "releases": [item]})
    with pytest.raises(ValueError):
        verify_catalog(blob, keys)


def test_source_dependency_requires_locked_wheel_build_tools():
    item = release()
    item["requirements"].append(
        {
            "name": "esptool",
            "version": "5.1.0",
            "filename": "esptool-5.1.0.tar.gz",
            "url": "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/esptool.tar.gz",
            "sha256": "b" * 64,
            "size": 100,
        }
    )
    blob, keys = signed({"schema_version": 1, "sequence": 1, "releases": [item]})
    with pytest.raises(ValueError, match="build"):
        verify_catalog(blob, keys)
    item["build_requirements"] = [
        {
            "name": "setuptools",
            "version": "80.0.0",
            "filename": "setuptools-80.0.0-py3-none-any.whl",
            "url": "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/setuptools.whl",
            "sha256": "c" * 64,
            "size": 100,
        }
    ]
    blob, keys = signed({"schema_version": 1, "sequence": 1, "releases": [item]})
    parsed = verify_catalog(blob, keys).releases[0]
    assert parsed.build_requirements[0].name == "setuptools"
    assert len(parsed.artifacts) == 3
