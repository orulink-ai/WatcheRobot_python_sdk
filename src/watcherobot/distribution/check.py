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
    application_protocol: str = ""
    supported_host_platforms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "id": self.app_id,
            "name": self.name,
            "version": self.version,
            "requires_watcherobot": self.requires_watcherobot,
            "dependencies": list(self.dependencies),
            "supported_host_platforms": list(self.supported_host_platforms),
            "description": self.description,
            "author": self.author,
            "icon": self.icon,
        }
        if self.schema_version == 3:
            result["requires_sdk"] = result.pop("requires_watcherobot")
            result["requires_daemon"] = {"application_protocol": self.application_protocol}
        return result


def check_application(
    application_dir: Path,
    *,
    watcherobot_version: str | None = None,
    daemon: bool = False,
) -> ApplicationCheckResult:
    """Validate one source directory through the canonical manifest loader."""

    manifest = ApplicationManifest.load(
        Path(application_dir),
        watcherobot_version=watcherobot_version,
        daemon=daemon,
    )
    collect_application_source_files(application_dir)
    if manifest.schema_version == 3:
        from .dependency_lock import read_dependency_lock

        read_dependency_lock(
            application_dir,
            manifest.requires_watcherobot,
            manifest.dependencies,
        )
    return ApplicationCheckResult(
        schema_version=manifest.schema_version,
        app_id=manifest.app_id,
        name=manifest.name,
        version=manifest.version,
        requires_watcherobot=manifest.requires_watcherobot,
        dependencies=manifest.dependencies,
        supported_host_platforms=manifest.supported_host_platforms,
        description=manifest.description,
        author=manifest.author,
        icon=manifest.icon,
        application_protocol=manifest.application_protocol,
    )
