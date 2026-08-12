from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from packaging.version import Version


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


def test_release_version_must_be_canonical_pep440() -> None:
    module = _load_module()

    assert module.validate_package_version("0.1.1a3") == Version("0.1.1a3")
    with pytest.raises(ValueError, match="canonical PEP 440"):
        module.validate_package_version("0.1.1-alpha3")


@pytest.mark.parametrize(
    ("current", "release_type", "expected"),
    [
        ("0.1.1a2", "prerelease", "0.1.1a3"),
        ("0.1.1a2", "stable", "0.1.1"),
        ("0.1.1", "prerelease", "0.1.2a1"),
        ("0.1.1", "minor", "0.2.0a1"),
        ("0.1.1", "major", "1.0.0a1"),
    ],
)
def test_next_release_version_is_deterministic(
    current: str,
    release_type: str,
    expected: str,
) -> None:
    module = _load_module()

    assert module.next_release_version(current, release_type) == expected


def test_stable_release_requires_a_prerelease() -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="requires a pre-release"):
        module.next_release_version("0.1.1", "stable")


def test_explicit_release_version_must_increase() -> None:
    module = _load_module()

    assert module.validate_version_increment("0.1.1a2", "0.1.1a3") == "0.1.1a3"
    with pytest.raises(ValueError, match="must be newer"):
        module.validate_version_increment("0.1.1a2", "0.1.1a2")


@pytest.mark.parametrize("tag", ["0.1.0a1", "v0.1.0", "release-0.1.0a1"])
def test_release_tag_mismatch_is_rejected(tag: str) -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="must match package version"):
        module.validate_release_tag(tag, "0.1.0a1")
