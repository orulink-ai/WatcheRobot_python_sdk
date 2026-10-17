import json

import pytest

from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
    parse_application_manifest,
)


def document(contracts):
    return json.dumps(
        {
            "schema_version": 3,
            "id": "test.application",
            "name": "Test",
            "version": "1.0.0",
            "requires_sdk": ">=0.1,<1",
            "requires_daemon": {"application_protocol": ">=1,<2"},
            "dependencies": [],
            "supported_host_platforms": ["macos", "windows"],
            "desktop_contracts": contracts,
        }
    ).encode()


def test_manifest_preserves_explicit_business_groups_without_default_application_rules():
    metadata = parse_application_manifest(document({"device_control": 1}))
    assert dict(metadata.desktop_contracts) == {"device_control": 1}
    assert metadata.to_dict()["desktop_contracts"] == {"device_control": 1}


@pytest.mark.parametrize(
    "value", [{"device_control": True}, {"device_control": 0}, [], {"../invalid": 1}]
)
def test_invalid_business_declarations_are_rejected(value):
    with pytest.raises(ApplicationManifestError):
        parse_application_manifest(document(value))
