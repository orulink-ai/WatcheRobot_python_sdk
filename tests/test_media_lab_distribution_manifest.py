from pathlib import Path

import pytest
from watcherobot.runtime.daemon.application.manifest import ApplicationManifest, ApplicationCompatibilityError

ROOT = Path(__file__).resolve().parents[1] / "examples" / "sdk_media_lab"


def test_test_bench_distribution_declares_runtime_dependencies():
    manifest = ApplicationManifest.load(ROOT, watcherobot_version="0.1.8")
    assert manifest.schema_version == 2
    assert manifest.supported_host_platforms == ("windows", "macos")
    assert {item.split(">=")[0] for item in manifest.dependencies} == {"fastapi", "uvicorn", "pydantic"}


def test_test_bench_rejects_sdk_before_distribution_contract():
    with pytest.raises(ApplicationCompatibilityError):
        ApplicationManifest.load(ROOT, watcherobot_version="0.1.6")
