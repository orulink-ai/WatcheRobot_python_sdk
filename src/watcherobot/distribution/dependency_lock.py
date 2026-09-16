"""Portable, fixed application dependencies carried by the published source commit."""

from __future__ import annotations

import json
from pathlib import Path
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name


def read_dependency_lock(
    source: Path,
    sdk_requirement: str,
    declared_dependencies: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Require exact pins for v3. Resolution happens when publishing, not updating Desktop."""
    path = source / "app.lock.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
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
        raise ValueError("app.lock.json schema_version must be 1")
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("app.lock.json must contain pinned dependencies")
    names: set[str] = set()
    pinned_versions: dict[str, str] = {}
    sdk_found = False
    for item in dependencies:
        if not isinstance(item, str):
            raise ValueError("lock dependency must be a string")
        requirement = Requirement(item)
        pins = list(requirement.specifier)
        if (
            requirement.url
            or requirement.marker
            or len(pins) != 1
            or pins[0].operator != "=="
            or "*" in pins[0].version
        ):
            raise ValueError(
                "lock dependencies must use exact versions without URLs or markers"
            )
        name = canonicalize_name(requirement.name)
        if name in names:
            raise ValueError("duplicate lock dependency")
        names.add(name)
        pinned_versions[name] = pins[0].version
        if name == "watcherobot":
            sdk_found = SpecifierSet(sdk_requirement).contains(pins[0].version)
    if not sdk_found:
        raise ValueError(
            "app.lock.json must pin a watcherobot version satisfying requires_sdk"
        )
    for item in declared_dependencies:
        requirement = Requirement(item)
        name = canonicalize_name(requirement.name)
        version = pinned_versions.get(name)
        if requirement.url or requirement.marker or version is None:
            raise ValueError(
                "schema 3 dependencies must be covered by portable lock pins"
            )
        if requirement.specifier and not requirement.specifier.contains(version):
            raise ValueError(
                "app.lock.json pin does not satisfy the declared dependency"
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
    read_dependency_lock(args.application, manifest.requires_watcherobot)


if __name__ == "__main__":
    main()
