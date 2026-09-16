"""Portable, fixed application dependencies carried by the published source commit."""

from __future__ import annotations

import json
from pathlib import Path
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from watcherobot.runtime.daemon.application.manifest import ApplicationManifestError


class DependencyLockError(ApplicationManifestError):
    """Stable Application validation failure for app.lock.json."""


def read_dependency_lock(
    source: Path,
    sdk_requirement: str,
    declared_dependencies: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Require exact pins for v3. Resolution happens when publishing, not updating Desktop."""
    path = source / "app.lock.json"
    try:
        document = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DependencyLockError(
            "missing Application dependency lock: run `watcherobot-app-lock` in the Application directory",
            code="app_lock_missing",
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise DependencyLockError(
            "unable to read app.lock.json; check the file permissions and UTF-8 encoding",
            code="app_lock_invalid",
        ) from exc
    try:
        payload = json.loads(document)
    except json.JSONDecodeError as exc:
        raise DependencyLockError(
            "app.lock.json must contain valid JSON; regenerate it with `watcherobot-app-lock`",
            code="app_lock_invalid",
        ) from exc
    return validate_dependency_lock(
        payload, sdk_requirement, declared_dependencies=declared_dependencies
    )


def validate_dependency_lock(
    payload: object,
    sdk_requirement: str,
    *,
    declared_dependencies: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Validate before publishing a lock, preserving the previous file on error."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise DependencyLockError(
            "app.lock.json schema_version must be 1; regenerate the dependency lock",
            code="app_lock_invalid",
        )
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise DependencyLockError(
            "app.lock.json must contain pinned dependencies; regenerate the dependency lock",
            code="app_lock_invalid",
        )
    names: set[str] = set()
    pinned_versions: dict[str, str] = {}
    sdk_found = False
    for item in dependencies:
        if not isinstance(item, str):
            raise DependencyLockError(
                "app.lock.json dependencies must be strings",
                code="app_lock_invalid",
            )
        try:
            requirement = Requirement(item)
        except InvalidRequirement as exc:
            raise DependencyLockError(
                "app.lock.json contains an invalid dependency requirement",
                code="app_lock_invalid",
            ) from exc
        pins = list(requirement.specifier)
        if (
            requirement.url
            or requirement.marker
            or requirement.extras
            or len(pins) != 1
            or pins[0].operator != "=="
            or "*" in pins[0].version
        ):
            raise DependencyLockError(
                "lock dependencies must use exact versions without URLs, markers, or extras; regenerate the dependency lock",
                code="app_lock_invalid",
            )
        name = canonicalize_name(requirement.name)
        if name in names:
            raise DependencyLockError(
                "app.lock.json contains a duplicate dependency",
                code="app_lock_invalid",
            )
        names.add(name)
        pinned_versions[name] = pins[0].version
        if name == "watcherobot":
            sdk_found = SpecifierSet(sdk_requirement).contains(pins[0].version)
    if not sdk_found:
        raise DependencyLockError(
            "app.lock.json must pin a watcherobot version satisfying requires_sdk; regenerate it in a compatible SDK environment",
            code="app_lock_incompatible",
        )
    for item in declared_dependencies:
        requirement = Requirement(item)
        name = canonicalize_name(requirement.name)
        version = pinned_versions.get(name)
        if (
            requirement.url
            or requirement.marker
            or requirement.extras
            or version is None
        ):
            raise DependencyLockError(
                "schema 3 dependencies must be covered by portable lock pins; regenerate app.lock.json",
                code="app_lock_incompatible",
            )
        if requirement.specifier and not requirement.specifier.contains(version):
            raise DependencyLockError(
                "app.lock.json pin does not satisfy the declared dependency; regenerate app.lock.json",
                code="app_lock_incompatible",
            )
    return tuple(dependencies)


def main() -> None:
    """Freeze the tested project environment using the same portable Python entry."""
    import argparse
    from importlib.metadata import distributions

    parser = argparse.ArgumentParser(
        description="Freeze this Application Python environment"
    )
    parser.add_argument("application", type=Path, nargs="?", default=Path.cwd())
    args = parser.parse_args()
    from watcherobot.runtime.daemon.application.manifest import ApplicationManifest

    manifest = ApplicationManifest.load(args.application)
    dependencies = sorted(
        {
            canonicalize_name(item.metadata["Name"]) + "==" + item.version
            for item in distributions()
            if item.metadata["Name"]
        }
    )
    target = args.application / "app.lock.json"
    validate_dependency_lock(
        {"schema_version": 1, "dependencies": dependencies},
        manifest.requires_watcherobot,
        declared_dependencies=manifest.dependencies,
    )
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dependencies": dependencies,
            },
            indent=2,
        )
        + chr(10),
        encoding="utf-8",
    )
    temporary.replace(target)
    read_dependency_lock(
        args.application,
        manifest.requires_watcherobot,
        manifest.dependencies,
    )


if __name__ == "__main__":
    main()
