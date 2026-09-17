from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from watcherobot.distribution.check import check_application
from watcherobot.distribution.dependency_lock import read_dependency_lock
from watcherobot.distribution.source_files import collect_application_source_files


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
    } == {"fastapi", "httpx", "numpy", "pydantic", "uvicorn"}

    locked = read_dependency_lock(
        root,
        application.requires_watcherobot,
        application.dependencies,
    )
    locked_names = {canonicalize_name(Requirement(item).name) for item in locked}
    assert {
        "watcherobot",
        "fastapi",
        "httpx",
        "numpy",
        "pydantic",
        "uvicorn",
    } <= locked_names


def test_first_meeting_publication_excludes_local_runtime_artifacts():
    root = Path(__file__).resolve().parents[1]
    artifact = root / "artifacts" / "settings.json"
    artifact.parent.mkdir(exist_ok=True)
    previous = artifact.read_bytes() if artifact.exists() else None
    try:
        artifact.write_text('{"api_key":"must-not-be-published"}', encoding="utf-8")
        published = {
            path.as_posix() for path in collect_application_source_files(root)
        }
    finally:
        if previous is None:
            artifact.unlink(missing_ok=True)
        else:
            artifact.write_bytes(previous)

    assert ".watcherignore" not in published
    assert not any(path.startswith("artifacts/") for path in published)
