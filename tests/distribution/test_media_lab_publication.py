from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from watcherobot.distribution.dependency_lock import read_dependency_lock
from watcherobot.distribution.check import check_application
from watcherobot.distribution.submit import _validate_submission_metadata


def test_bundled_ui_example_is_ready_for_catalog_submission():
    root = Path(__file__).resolve().parents[2] / "examples" / "sdk_media_lab"
    application = check_application(root)
    _validate_submission_metadata(application)
    assert application.schema_version == 3
    assert application.requires_watcherobot == ">=0.1.9,<0.2"
    assert application.application_protocol == ">=1,<2"
    assert application.supported_host_platforms == ("windows",)
    assert {
        canonicalize_name(Requirement(item).name)
        for item in application.dependencies
    } == {
        "fastapi",
        "pydantic",
        "uvicorn",
    }

    locked = read_dependency_lock(
        root,
        application.requires_watcherobot,
        application.dependencies,
    )
    locked_names = {canonicalize_name(Requirement(item).name) for item in locked}
    assert {"watcherobot", "fastapi", "pydantic", "uvicorn"} <= locked_names
