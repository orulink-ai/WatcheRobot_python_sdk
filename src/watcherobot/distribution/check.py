"""Local Application project validation shared by CLI and Desktop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from watcherobot.runtime.daemon.application.manifest import ApplicationManifest

from .source_files import collect_application_source_files


@dataclass(frozen=True)
class ApplicationCheckResult:
    """Validated, non-sensitive metadata from the single app.json schema."""

    schema_version: int
    app_id: str
    name: str
    version: str
    requires_watcherobot: str
    dependencies: tuple[str, ...]
    description: str
    author: str
    icon: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "id": self.app_id,
            "name": self.name,
            "version": self.version,
            "requires_watcherobot": self.requires_watcherobot,
            "dependencies": list(self.dependencies),
            "description": self.description,
            "author": self.author,
            "icon": self.icon,
        }


def check_application(
    application_dir: Path,
    *,
    watcherobot_version: str | None = None,
) -> ApplicationCheckResult:
    """Validate one source directory through the canonical manifest loader."""

    manifest = ApplicationManifest.load(
        Path(application_dir),
        watcherobot_version=watcherobot_version,
    )
    collect_application_source_files(application_dir)
    return ApplicationCheckResult(
        schema_version=manifest.schema_version,
        app_id=manifest.app_id,
        name=manifest.name,
        version=manifest.version,
        requires_watcherobot=manifest.requires_watcherobot,
        dependencies=manifest.dependencies,
        description=manifest.description,
        author=manifest.author,
        icon=manifest.icon,
    )
