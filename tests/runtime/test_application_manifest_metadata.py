from __future__ import annotations

import json

import pytest

from watcherobot.runtime.daemon.application.manifest import (
    ApplicationCompatibilityError,
    ApplicationManifestError,
    ApplicationManifestMetadata,
    parse_application_manifest,
)


def _document(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "schema_version": 1,
        "id": "com.orulink.remote_demo",
        "name": "Remote Demo",
        "version": "1.2.3",
        "requires_watcherobot": ">=1.0,<2.0",
        "dependencies": ["httpx>=0.28,<1"],
        "description": "Remote metadata",
        "author": "Orulink",
        "icon": "assets/icon.png",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_parse_remote_manifest_reuses_schema_without_local_source_files() -> None:
    metadata = parse_application_manifest(
        _document(),
        watcherobot_version="1.5.0",
    )

    assert metadata == ApplicationManifestMetadata(
        schema_version=1,
        app_id="com.orulink.remote_demo",
        name="Remote Demo",
        version="1.2.3",
        requires_watcherobot=">=1.0,<2.0",
        dependencies=("httpx>=0.28,<1",),
        description="Remote metadata",
        author="Orulink",
        icon="assets/icon.png",
    )
    assert metadata.to_dict()["id"] == "com.orulink.remote_demo"


@pytest.mark.parametrize(
    "document",
    [
        b"not json",
        b"\xff",
        _document(unknown="field"),
        _document(dependencies=["not a requirement ???"]),
    ],
)
def test_parse_remote_manifest_rejects_the_same_invalid_schema(
    document: bytes,
) -> None:
    with pytest.raises(ApplicationManifestError):
        parse_application_manifest(document, watcherobot_version="1.5.0")


def test_parse_remote_manifest_enforces_sdk_compatibility() -> None:
    with pytest.raises(ApplicationCompatibilityError):
        parse_application_manifest(
            _document(requires_watcherobot=">=2.0,<3.0"),
            watcherobot_version="1.5.0",
        )
