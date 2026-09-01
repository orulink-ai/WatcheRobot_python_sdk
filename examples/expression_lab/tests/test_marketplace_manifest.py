from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_expression_lab_has_publishable_marketplace_manifest() -> None:
    manifest = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))

    assert manifest == {
        "schema_version": 2,
        "id": "com.orulink.expression_lab",
        "name": "Watcher Expression Lab",
        "version": "0.1.0",
        "author": "Orulink AI",
        "description": (
            "Design, preview, and run procedural KuroBlob-style Watcher "
            "expressions and vector decorations."
        ),
        "requires_watcherobot": ">=0.1.4,<0.2",
        "dependencies": ["fastapi>=0.129,<1", "uvicorn>=0.30,<1"],
        "supported_host_platforms": ["windows"],
        "icon": "icon.svg",
    }

    icon = ROOT / manifest["icon"]
    assert icon.is_file()
    assert ET.parse(icon).getroot().tag == "{http://www.w3.org/2000/svg}svg"
