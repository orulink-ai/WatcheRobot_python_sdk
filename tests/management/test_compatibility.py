from __future__ import annotations

import pytest

from watcherobot.management.compatibility import (
    check_application_daemon,
    check_desktop,
    runtime_contracts,
)


def requirements() -> dict[str, str]:
    return {"manager": ">=1,<2", "management": ">=1,<2", "distribution": ">=1,<2"}


def test_desktop_checks_contracts_not_sdk_version_equality() -> None:
    candidate = runtime_contracts()
    assert check_desktop(requirements(), candidate).status == "compatible"


def test_missing_contract_is_unverified_not_compatible() -> None:
    candidate = runtime_contracts()
    del candidate["distribution"]
    result = check_desktop(requirements(), candidate)
    assert result.status == "unverified"
    assert result.issues[0].relation == "desktop.distribution"
    assert result.issues[0].code == "contract_missing"


def test_future_management_contract_is_rejected() -> None:
    candidate = runtime_contracts()
    candidate["management"] = 2
    result = check_desktop(requirements(), candidate)
    assert result.status == "incompatible"
    assert result.to_dict()["issues"][0]["required"] == ">=1,<2"
    assert result.to_dict()["issues"][0]["actual"] == "2"


@pytest.mark.parametrize("candidate", [True, -1, "1", 1.5, None])
def test_invalid_contract_identity_is_not_accepted(candidate: object) -> None:
    descriptor = runtime_contracts()
    descriptor["management"] = candidate
    assert check_desktop(requirements(), descriptor).status != "compatible"


@pytest.mark.parametrize(
    "consumer", [{}, {"management": ">=1"}, {**requirements(), "other": "==1"}]
)
def test_desktop_must_declare_complete_known_contracts(
    consumer: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        check_desktop(consumer, runtime_contracts())


def test_application_daemon_protocol_is_independent_of_application_sdk() -> None:
    assert check_application_daemon(">=1,<2").status == "compatible"
    assert check_application_daemon(">=2,<3").status == "incompatible"


def test_business_groups_are_independent_and_missing_declarations_are_unverified():
    from watcherobot.management.compatibility import check_desktop_application

    result = check_desktop_application(
        {"device_control": ">=1,<2", "application_settings": ">=1,<2"},
        {"device_control": 1},
    )
    assert result["device_control"].status == "compatible"
    assert result["application_settings"].status == "unverified"
    assert (
        check_desktop_application({"device_control": ">=1,<2"}, {"device_control": 2})[
            "device_control"
        ].status
        == "incompatible"
    )
    assert (
        check_desktop_application(
            {"device_control": ">=1,<2"}, {"device_control": True}
        )["device_control"].status
        == "unverified"
    )
