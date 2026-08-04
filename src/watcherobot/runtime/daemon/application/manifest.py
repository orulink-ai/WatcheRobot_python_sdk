"""Application directory manifest loading and Runtime compatibility checks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


_VERSION_PATTERN = re.compile(
    r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$"
)
_APPLICATION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "id",
        "name",
        "version",
        "requires_watcherobot",
        "dependencies",
    }
)
_OPTIONAL_FIELDS = frozenset({"description", "author", "icon"})
_ALLOWED_FIELDS = _REQUIRED_FIELDS | _OPTIONAL_FIELDS


class ApplicationManifestError(ValueError):
    """Raised when an Application directory doesn't satisfy app.json rules."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "app_manifest_invalid",
    ) -> None:
        self.code = code
        super().__init__(message)


class ApplicationCompatibilityError(ApplicationManifestError):
    """Raised when an Application cannot run on the installed watcherobot."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="app_sdk_incompatible")


@dataclass(frozen=True)
class ApplicationManifest:
    schema_version: int
    app_id: str
    name: str
    version: str
    requires_watcherobot: str
    dependencies: tuple[str, ...]
    entrypoint: Path
    description: str = ""
    author: str = ""
    icon: str = ""

    @classmethod
    def load(
        cls,
        application_dir: Path,
        *,
        watcherobot_version: str | None = None,
    ) -> "ApplicationManifest":
        application_root = Path(application_dir).resolve()
        manifest_path = application_root / "app.json"
        entrypoint_path = application_root / "app.py"
        if not manifest_path.is_file():
            raise ApplicationManifestError(
                f"missing Application manifest: {manifest_path}",
                code="app_manifest_missing",
            )
        if not entrypoint_path.is_file():
            raise ApplicationManifestError(
                f"missing Application entrypoint: {entrypoint_path}",
                code="app_entrypoint_missing",
            )

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplicationManifestError(
                f"invalid Application manifest: {manifest_path}"
            ) from exc
        if not isinstance(payload, dict):
            raise ApplicationManifestError("Application manifest must be an object")

        unknown_fields = sorted(set(payload) - _ALLOWED_FIELDS)
        missing_fields = sorted(_REQUIRED_FIELDS - set(payload))
        if unknown_fields:
            raise ApplicationManifestError(
                f"unknown fields: {', '.join(unknown_fields)}"
            )
        if missing_fields:
            raise ApplicationManifestError(
                f"missing fields: {', '.join(missing_fields)}"
            )

        schema_version = payload.get("schema_version")
        app_id = str(payload.get("id") or "").strip()
        name = str(payload.get("name") or "").strip()
        version = str(payload.get("version") or "").strip()
        requires_watcherobot = str(
            payload.get("requires_watcherobot") or ""
        ).strip()
        dependencies = payload.get("dependencies")

        if schema_version != 1:
            raise ApplicationManifestError("schema_version must be 1")
        if _APPLICATION_ID_PATTERN.fullmatch(app_id) is None:
            raise ApplicationManifestError(
                "id must use 1-64 lowercase letters, digits, dot, underscore, or dash"
            )
        if not name:
            raise ApplicationManifestError("name must not be empty")
        if not _VERSION_PATTERN.fullmatch(version):
            raise ApplicationManifestError(
                "version must use semantic version format"
            )
        if not requires_watcherobot:
            raise ApplicationManifestError(
                "requires_watcherobot must not be empty"
            )
        try:
            requirement = SpecifierSet(requires_watcherobot)
        except InvalidSpecifier as exc:
            raise ApplicationManifestError(
                "requires_watcherobot must be a valid version range"
            ) from exc
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item.strip()
            for item in dependencies
        ):
            raise ApplicationManifestError(
                "dependencies must be an array of non-empty strings",
                code="app_dependency_invalid",
            )
        normalized_dependencies: list[str] = []
        for index, dependency in enumerate(dependencies):
            normalized = dependency.strip()
            try:
                Requirement(normalized)
            except InvalidRequirement as exc:
                raise ApplicationManifestError(
                    f"dependencies[{index}] must be a valid Python requirement",
                    code="app_dependency_invalid",
                ) from exc
            normalized_dependencies.append(normalized)

        installed_version = watcherobot_version or _installed_watcherobot_version()
        try:
            parsed_version = Version(installed_version)
        except InvalidVersion as exc:
            raise ApplicationCompatibilityError(
                f"installed watcherobot version is invalid: {installed_version}"
            ) from exc
        if not requirement.contains(parsed_version, prereleases=True):
            raise ApplicationCompatibilityError(
                f"Application requires watcherobot {requires_watcherobot}, "
                f"installed {installed_version}"
            )

        icon = str(payload.get("icon") or "").strip()
        if icon:
            icon_path = (application_root / icon).resolve()
            try:
                icon_path.relative_to(application_root)
            except ValueError as exc:
                raise ApplicationManifestError(
                    "icon path must stay inside the Application directory"
                ) from exc
            if not icon_path.is_file():
                raise ApplicationManifestError(f"icon does not exist: {icon}")

        return cls(
            schema_version=1,
            app_id=app_id,
            name=name,
            version=version,
            requires_watcherobot=requires_watcherobot,
            dependencies=tuple(normalized_dependencies),
            entrypoint=entrypoint_path,
            description=str(payload.get("description") or "").strip(),
            author=str(payload.get("author") or "").strip(),
            icon=icon,
        )


def _installed_watcherobot_version() -> str:
    from watcherobot import __version__

    return __version__
