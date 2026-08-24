"""机器人闹钟的应用商店发布元数据门禁。"""

from __future__ import annotations

import json
from pathlib import Path


APPLICATION_DIR = Path(__file__).resolve().parents[1]
MANIFEST_PATH = APPLICATION_DIR / "app.json"


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_is_ready_for_marketplace_submission() -> None:
    manifest = _manifest()

    assert manifest["schema_version"] == 2
    assert manifest["id"] == "com.orulink.robot_alarm"
    assert manifest["name"] == "Robot Alarm"
    assert manifest["supported_host_platforms"] == ["windows", "macos"]
    assert manifest["description"]
    assert manifest["author"] == "Orulink AI"


def test_manifest_icon_is_a_publishable_local_svg() -> None:
    manifest = _manifest()
    icon = manifest["icon"]

    assert isinstance(icon, str)
    assert icon.endswith(".svg")
    icon_path = APPLICATION_DIR / icon
    assert icon_path.is_file()
    assert "<svg" in icon_path.read_text(encoding="utf-8")
