from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def _load_module():
    path = ROOT / "tools" / "check_release_availability.py"
    spec = importlib.util.spec_from_file_location("check_release_availability", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_absent_canonical_version_is_accepted() -> None:
    module = _load_module()
    module.validate_absent(
        "0.1.1a3",
        pypi_status=404,
        testpypi_status=404,
        release_exists=False,
        tag_exists=False,
    )


@pytest.mark.parametrize(
    ("pypi_status", "testpypi_status", "release_exists", "tag_exists"),
    [(200, 404, False, False), (404, 200, False, False), (404, 404, True, False), (404, 404, False, True)],
)
def test_existing_version_is_rejected(
    pypi_status: int, testpypi_status: int, release_exists: bool, tag_exists: bool
) -> None:
    module = _load_module()
    with pytest.raises(ValueError, match="already exists"):
        module.validate_absent(
            "0.1.1a3",
            pypi_status=pypi_status,
            testpypi_status=testpypi_status,
            release_exists=release_exists,
            tag_exists=tag_exists,
        )


def test_noncanonical_pep440_version_is_rejected() -> None:
    module = _load_module()
    with pytest.raises(ValueError, match="canonical"):
        module.validate_absent(
            "0.1.1-alpha3",
            pypi_status=404,
            testpypi_status=404,
            release_exists=False,
            tag_exists=False,
        )


def test_github_lookup_fails_closed_for_operational_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()

    class Result:
        returncode = 1
        stdout = ""
        stderr = "HTTP 500: internal server error"

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError, match="GitHub lookup failed"):
        module.github_resource_exists("orulink-ai/WatcheRobot_python_sdk", "releases/tags/v0.1.1a4")


def test_github_lookup_accepts_only_verified_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()

    class Result:
        returncode = 1
        stdout = ""
        stderr = "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())
    assert not module.github_resource_exists(
        "orulink-ai/WatcheRobot_python_sdk", "git/ref/tags/v0.1.1a4"
    )
