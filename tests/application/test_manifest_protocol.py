import json
from pathlib import Path

import pytest

from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifest,
    ApplicationManifestError,
    parse_application_manifest,
)


def manifest(protocol: str = ">=1,<2") -> dict:
    return dict(
        schema_version=3,
        id="example.test",
        name="Test",
        version="1.0.0",
        requires_sdk="==0.1.7",
        requires_daemon={"application_protocol": protocol},
        supported_host_platforms=["windows", "macos"],
        dependencies=[],
    )


def test_daemon_checks_protocol_without_binding_application_sdk(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text(json.dumps(manifest()), encoding="utf-8")
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    assert ApplicationManifest.load(tmp_path, daemon=True).schema_version == 3
    with pytest.raises(ApplicationManifestError):
        ApplicationManifest.load(tmp_path, watcherobot_version="0.1.9")


def test_future_protocol_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text(json.dumps(manifest(">=2,<3")), encoding="utf-8")
    (tmp_path / "app.py").write_text("", encoding="utf-8")
    with pytest.raises(ApplicationManifestError, match="protocol"):
        ApplicationManifest.load(tmp_path, daemon=True)


def test_schema_three_roundtrip() -> None:
    metadata = parse_application_manifest(json.dumps(manifest()).encode())
    payload = metadata.to_dict()
    assert payload["requires_sdk"] == "==0.1.7"
    assert "requires_watcherobot" not in payload
    assert parse_application_manifest(json.dumps(payload).encode()) == metadata
