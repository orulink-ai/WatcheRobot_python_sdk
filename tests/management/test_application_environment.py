import pytest

from watcherobot.management.application_environment import verify_probe


def test_application_sdk_is_checked_against_app_not_daemon():
    assert (
        verify_probe(
            {"sdk_version": "0.1.7", "dependencies_ok": True},
            ">=0.1.7,<0.2",
            ("watcherobot==0.1.7",),
        )["status"]
        == "compatible"
    )


def test_missing_dependency_or_wrong_locked_sdk_blocks_start():
    with pytest.raises(ValueError):
        verify_probe(
            {"sdk_version": "0.1.7", "dependencies_ok": False}, ">=0.1.7,<0.2", ()
        )
    with pytest.raises(ValueError):
        verify_probe(
            {"sdk_version": "0.1.8", "dependencies_ok": True},
            ">=0.1.7,<0.2",
            ("watcherobot==0.1.7",),
        )
