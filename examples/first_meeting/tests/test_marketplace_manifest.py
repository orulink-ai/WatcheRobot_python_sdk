from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from watcherobot.distribution.check import check_application
from watcherobot.distribution.dependency_lock import read_dependency_lock


def test_first_meeting_targets_current_shared_runtime_contract():
    root = Path(__file__).resolve().parents[1]
    application = check_application(root)

    assert application.schema_version == 3
    assert application.requires_watcherobot == ">=0.1.9,<0.2"
    assert application.application_protocol == ">=1,<2"
    assert application.supported_host_platforms == ("windows",)
    assert {
        canonicalize_name(Requirement(item).name)
        for item in application.dependencies
    } == {"httpx", "numpy", "pydantic"}

    locked = read_dependency_lock(
        root,
        application.requires_watcherobot,
        application.dependencies,
    )
    locked_names = {canonicalize_name(Requirement(item).name) for item in locked}
    assert {"watcherobot", "httpx", "numpy", "pydantic"} <= locked_names
