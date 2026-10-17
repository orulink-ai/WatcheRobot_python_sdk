"""One compatibility policy shared by SDK tools, Daemon and Desktop callers.

Contract versions describe behavior, not package releases. Adding optional fields
does not change a contract; breaking its documented behavior requires a new one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet

MANAGER_CONTRACT = 1
MANAGEMENT_CONTRACT = 1
DISTRIBUTION_CONTRACT = 1
APPLICATION_CONTRACT = 1
STATE_SCHEMA = 1

CompatibilityStatus = Literal["compatible", "incompatible", "unverified"]
_DESKTOP_CONTRACTS = frozenset({"manager", "management", "distribution"})


def runtime_contracts() -> dict[str, object]:
    """Describe this SDK's actual implemented contracts."""
    return {
        "manager": MANAGER_CONTRACT,
        "management": MANAGEMENT_CONTRACT,
        "distribution": DISTRIBUTION_CONTRACT,
        "application": APPLICATION_CONTRACT,
        "state": STATE_SCHEMA,
    }


@dataclass(frozen=True)
class CompatibilityIssue:
    relation: str
    code: str
    required: str
    actual: str | None
    action: str


@dataclass(frozen=True)
class CompatibilityReport:
    status: CompatibilityStatus
    issues: tuple[CompatibilityIssue, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "issues": [asdict(item) for item in self.issues]}


def _require_range(value: object) -> SpecifierSet:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A nonempty contract requirement is required")
    try:
        return SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise ValueError("Invalid contract requirement") from exc


def _compare(relation: str, required: str, actual: object) -> CompatibilityIssue | None:
    requirement = _require_range(required)
    if type(actual) is not int or actual < 1:
        return CompatibilityIssue(
            relation, "contract_missing", required, None, "update_component"
        )
    if not requirement.contains(str(actual)):
        return CompatibilityIssue(
            relation, "contract_mismatch", required, str(actual), "update_component"
        )
    return None


def _report(issues: tuple[CompatibilityIssue, ...]) -> CompatibilityReport:
    if any(item.code == "contract_mismatch" for item in issues):
        return CompatibilityReport("incompatible", issues)
    return CompatibilityReport("unverified" if issues else "compatible", issues)


def check_desktop(
    requirements: Mapping[str, str], actual: Mapping[str, object]
) -> CompatibilityReport:
    """Reject incomplete caller declarations rather than guessing compatibility."""
    if set(requirements) != _DESKTOP_CONTRACTS:
        raise ValueError(
            "Desktop must declare manager, management and distribution contracts"
        )
    issues = tuple(
        issue
        for name in sorted(_DESKTOP_CONTRACTS)
        if (issue := _compare(f"desktop.{name}", requirements[name], actual.get(name)))
        is not None
    )
    return _report(issues)


def check_application_daemon(
    requirement: str, actual: object = APPLICATION_CONTRACT
) -> CompatibilityReport:
    """Check channel behavior without binding the application's SDK release."""
    issue = _compare("application.daemon", requirement, actual)
    return _report((issue,) if issue else ())


def check_desktop_application(
    requirements: Mapping[str, str], actual: Mapping[str, object] | None
) -> dict[str, CompatibilityReport]:
    """Compare opaque business groups; never inspect or route business frames."""
    if len(requirements) > 32:
        raise ValueError("Too many Desktop business contract groups")
    groups = actual or {}
    result = {}
    for group, requirement in requirements.items():
        issue = _compare(f"desktop.application.{group}", requirement, groups.get(group))
        result[group] = _report((issue,) if issue else ())
    return result


def parse_business_contracts(value: object) -> tuple[tuple[str, int], ...]:
    """Validate Application declarations without inventing missing capabilities."""
    import re

    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("desktop_contracts must be an object with at most 32 groups")
    for group, version in value.items():
        if not isinstance(group, str) or not re.fullmatch(
            "[a-z][a-z0-9_.-]{0,63}", group
        ):
            raise ValueError("Invalid Desktop business contract group")
        if type(version) is not int or version < 1:
            raise ValueError(
                "Desktop business contract versions must be positive integers"
            )
    return tuple(sorted(value.items()))
