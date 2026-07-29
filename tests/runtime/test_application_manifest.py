from __future__ import annotations

import json

import pytest

from watcherobot.runtime.daemon.application.manifest import (
    ApplicationCompatibilityError,
    ApplicationManifest,
    ApplicationManifestError,
)


def write_application(
    root,
    *,
    app_id: str = "com.orulink.demo",
    requires_watcherobot: str | None = ">=1.0,<2.0",
    extra: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "id": app_id,
        "name": "Demo",
        "version": "1.0.0",
        "dependencies": [],
    }
    if requires_watcherobot is not None:
        payload["requires_watcherobot"] = requires_watcherobot
    if extra:
        payload.update(extra)
    root.joinpath("app.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text("print('demo')\n", encoding="utf-8")


def test_manifest_requires_fixed_app_py_and_compatible_watcherobot(tmp_path) -> None:
    write_application(tmp_path)

    manifest = ApplicationManifest.load(
        tmp_path,
        watcherobot_version="1.5.0",
    )

    assert manifest.app_id == "com.orulink.demo"
    assert manifest.version == "1.0.0"
    assert manifest.requires_watcherobot == ">=1.0,<2.0"
    assert manifest.entrypoint == tmp_path / "app.py"


def test_manifest_rejects_missing_watcherobot_requirement(tmp_path) -> None:
    write_application(tmp_path, requires_watcherobot=None)

    with pytest.raises(
        ApplicationManifestError,
        match="requires_watcherobot",
    ):
        ApplicationManifest.load(tmp_path, watcherobot_version="1.5.0")


def test_manifest_rejects_incompatible_watcherobot_version(tmp_path) -> None:
    write_application(tmp_path, requires_watcherobot=">=2.0,<3.0")

    with pytest.raises(
        ApplicationCompatibilityError,
        match="requires watcherobot",
    ):
        ApplicationManifest.load(tmp_path, watcherobot_version="1.5.0")


def test_manifest_rejects_arbitrary_entrypoint_override(tmp_path) -> None:
    write_application(tmp_path, extra={"entrypoint": "other.py"})
    tmp_path.joinpath("other.py").write_text("print('other')\n", encoding="utf-8")

    with pytest.raises(ApplicationManifestError, match="unknown fields"):
        ApplicationManifest.load(tmp_path, watcherobot_version="1.5.0")


def test_manifest_rejects_invalid_requirement_syntax(tmp_path) -> None:
    write_application(tmp_path, requires_watcherobot="not a version")

    with pytest.raises(
        ApplicationManifestError,
        match="requires_watcherobot",
    ):
        ApplicationManifest.load(tmp_path, watcherobot_version="1.5.0")


@pytest.mark.parametrize(
    "app_id",
    ["../escape", "UPPER", "space app", "", "a" * 65],
)
def test_manifest_rejects_application_ids_that_are_not_catalog_safe(
    tmp_path,
    app_id: str,
) -> None:
    write_application(tmp_path, app_id=app_id)

    with pytest.raises(ApplicationManifestError, match="id"):
        ApplicationManifest.load(tmp_path, watcherobot_version="1.5.0")
