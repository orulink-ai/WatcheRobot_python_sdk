from __future__ import annotations

import json
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


def test_expression_lab_has_publishable_marketplace_manifest() -> None:
    manifest = json.loads((ROOT / "app.json").read_text(encoding="utf-8"))

    assert manifest == {
        "schema_version": 2,
        "id": "com.orulink.expression_lab",
        "name": "Watcher Expression Lab",
        "version": "0.1.1",
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


def test_expression_lab_bundles_pr_199_firmware_with_verifiable_provenance() -> None:
    package_manifest = json.loads(
        (ROOT / "firmware" / "firmware-package.json").read_text(encoding="utf-8")
    )
    package_path = ROOT / "firmware" / package_manifest["filename"]
    payload = package_path.read_bytes()

    assert package_manifest["app_id"] == "com.orulink.expression_lab"
    assert package_manifest["app_version"] == "0.1.1"
    assert package_manifest["required_capability"] == "expression.runtime.v3"
    assert package_manifest["size_bytes"] == len(payload)
    assert package_manifest["sha256"] == hashlib.sha256(payload).hexdigest()
    assert package_manifest["source"] == {
        "repository": "https://github.com/orulink-ai/WatcheRobot_esp32",
        "pull_request": 199,
        "commit": "c4807d1a0b7d912ea05abcb578b10f5d7ea0a2e4",
    }

    with ZipFile(package_path) as archive:
        flash_args = archive.read("flash_args.txt").decode("utf-8")
        build_manifest = json.loads(archive.read("gate0-build-manifest.json"))

    assert "--flash-size 32MB" in flash_args
    assert len(build_manifest["flash_images"]) == 6
    assert build_manifest["source"] == package_manifest["source"]
