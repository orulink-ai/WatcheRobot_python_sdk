"""Official Application marketplace reads independent from Hub transport."""

from __future__ import annotations

from dataclasses import dataclass

from watcherobot import __version__
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
    ApplicationManifestMetadata,
    parse_application_manifest,
)

from .catalog_submission import (
    CATALOG_PATH,
    CATALOG_REPO_ID,
    CatalogDocumentError,
    parse_catalog_entries,
)
from .events import ErrorCode, EventSink, ProgressEvent
from .ports import HubError, MarketplaceHubClient


class MarketplaceError(RuntimeError):
    """Sanitized official-marketplace failure with a stable error code."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True)
class MarketplaceApplication:
    """One reviewed fixed Application version for Desktop presentation."""

    space_id: str
    commit: str
    source_url: str
    schema_version: int
    app_id: str
    name: str
    version: str
    requires_watcherobot: str
    dependencies: tuple[str, ...]
    description: str
    author: str
    icon: str
    compatible: bool

    @classmethod
    def from_metadata(
        cls,
        *,
        space_id: str,
        commit: str,
        metadata: ApplicationManifestMetadata,
        watcherobot_version: str,
    ) -> MarketplaceApplication:
        return cls(
            space_id=space_id,
            commit=commit,
            source_url=(
                f"https://huggingface.co/spaces/{space_id}/tree/{commit}"
            ),
            schema_version=metadata.schema_version,
            app_id=metadata.app_id,
            name=metadata.name,
            version=metadata.version,
            requires_watcherobot=metadata.requires_watcherobot,
            dependencies=metadata.dependencies,
            description=metadata.description,
            author=metadata.author,
            icon=metadata.icon,
            compatible=metadata.supports_watcherobot(watcherobot_version),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "space_id": self.space_id,
            "commit": self.commit,
            "source_url": self.source_url,
            "schema_version": self.schema_version,
            "id": self.app_id,
            "name": self.name,
            "version": self.version,
            "requires_watcherobot": self.requires_watcherobot,
            "dependencies": list(self.dependencies),
            "description": self.description,
            "author": self.author,
            "icon": self.icon,
            "compatible": self.compatible,
        }


@dataclass(frozen=True)
class OfficialMarketplace:
    """One successfully loaded immutable view of the official catalog."""

    catalog_commit: str
    applications: tuple[MarketplaceApplication, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog_commit": self.catalog_commit,
            "applications": [app.to_dict() for app in self.applications],
        }


class _NullEvents:
    def emit(self, event: object) -> None:
        del event


def load_official_marketplace(
    *,
    hub: MarketplaceHubClient,
    events: EventSink | None = None,
    watcherobot_version: str | None = None,
) -> OfficialMarketplace:
    """Read the official list and each app.json at its reviewed commit."""

    sink: EventSink = events or _NullEvents()
    sink.emit(
        ProgressEvent(
            stage="fetching_catalog",
            message="Loading official Application marketplace",
        )
    )
    try:
        document = hub.read_public_catalog(
            repo_id=CATALOG_REPO_ID,
            path=CATALOG_PATH,
        )
    except HubError as exc:
        raise MarketplaceError(
            ErrorCode.REMOTE_ERROR,
            "Unable to load the latest Application marketplace",
        ) from exc

    try:
        entries = parse_catalog_entries(document.content)
    except CatalogDocumentError as exc:
        raise MarketplaceError(
            ErrorCode.CATALOG_INVALID,
            "The official Application marketplace is invalid",
        ) from exc

    installed_version = watcherobot_version or __version__
    applications: list[MarketplaceApplication] = []
    seen_app_ids: set[str] = set()
    for entry in entries:
        source_details: dict[str, object] = {
            "space_id": entry.space_id,
            "commit": entry.commit,
        }
        sink.emit(
            ProgressEvent(
                stage="reading_manifest",
                message="Reading immutable Application metadata",
                data=source_details,
            )
        )
        try:
            manifest_document = hub.read_space_file(
                space_id=entry.space_id,
                commit=entry.commit,
                path="app.json",
            )
        except HubError as exc:
            raise MarketplaceError(
                ErrorCode.REMOTE_ERROR,
                "Unable to load the latest Application marketplace",
                details=source_details,
            ) from exc
        try:
            metadata = parse_application_manifest(manifest_document)
        except ApplicationManifestError as exc:
            raise MarketplaceError(
                ErrorCode.CATALOG_INVALID,
                "An Application manifest in the official marketplace is invalid",
                details=source_details,
            ) from exc
        expected_space_id = f"WatcherRobot-{metadata.app_id}"
        if entry.space_id.split("/", 1)[1] != expected_space_id:
            raise MarketplaceError(
                ErrorCode.CATALOG_INVALID,
                "A marketplace Space does not match its Application ID",
                details={**source_details, "id": metadata.app_id},
            )
        if metadata.app_id in seen_app_ids:
            raise MarketplaceError(
                ErrorCode.CATALOG_INVALID,
                "The official marketplace contains a duplicate Application ID",
                details={**source_details, "id": metadata.app_id},
            )
        seen_app_ids.add(metadata.app_id)
        applications.append(
            MarketplaceApplication.from_metadata(
                space_id=entry.space_id,
                commit=entry.commit,
                metadata=metadata,
                watcherobot_version=installed_version,
            )
        )

    return OfficialMarketplace(
        catalog_commit=document.commit,
        applications=tuple(applications),
    )
