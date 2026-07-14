from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def _load_module():
    path = ROOT / "tools" / "check_release_version.py"
    spec = importlib.util.spec_from_file_location("check_release_version", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_tag_matches_package_version() -> None:
    module = _load_module()

    assert module.validate_release_tag("v0.1.0a1", "0.1.0a1") == "0.1.0a1"


@pytest.mark.parametrize("tag", ["0.1.0a1", "v0.1.0", "release-0.1.0a1"])
def test_release_tag_mismatch_is_rejected(tag: str) -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="must match package version"):
        module.validate_release_tag(tag, "0.1.0a1")
