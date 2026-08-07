"""Validated fixed-revision snapshot delivery into caller-owned staging."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .catalog_submission import (
    CatalogDocumentError,
    validate_catalog_reference,
)
from .check import ApplicationCheckResult, check_application
from .events import ErrorCode, EventSink, ProgressEvent
from .ports import HubError, MarketplaceHubClient
from .source_files import ApplicationSourceError


MAX_SNAPSHOT_FILES = 1000
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024


class DownloadError(RuntimeError):
    """Sanitized fixed-snapshot failure with a stable machine error code."""

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
class DownloadResult:
    """One validated fixed snapshot delivered to caller staging."""

    space_id: str
    commit: str
    source_url: str
    target: Path
    application: ApplicationCheckResult

    def to_dict(self) -> dict[str, object]:
        return {
            "space_id": self.space_id,
            "commit": self.commit,
            "source_url": self.source_url,
            "target": str(self.target),
            "application": self.application.to_dict(),
        }


class _NullEvents:
    def emit(self, event: object) -> None:
        del event


def download_application_snapshot(
    *,
    space_id: str,
    commit: str,
    target: Path,
    hub: MarketplaceHubClient,
    events: EventSink | None = None,
    watcherobot_version: str | None = None,
) -> DownloadResult:
    """Download, validate and then deliver one immutable Space snapshot."""

    destination = _require_empty_target(target)
    try:
        reference = validate_catalog_reference(space_id, commit)
    except CatalogDocumentError as exc:
        raise DownloadError(
            ErrorCode.CATALOG_INVALID,
            "Application source must use a valid Space and complete commit",
        ) from exc

    sink: EventSink = events or _NullEvents()
    details: dict[str, object] = {
        "space_id": reference.space_id,
        "commit": reference.commit,
    }
    sink.emit(
        ProgressEvent(
            stage="downloading_snapshot",
            message="Downloading immutable Application source",
            data=details,
        )
    )
    temporary_directory = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.download-",
        dir=destination.parent,
    ))
    try:
        isolated_target = temporary_directory / "snapshot"
        isolated_target.mkdir()
        try:
            revision = hub.download_space_snapshot(
                space_id=reference.space_id,
                commit=reference.commit,
                target=isolated_target,
            )
        except HubError as exc:
            raise DownloadError(
                ErrorCode.REMOTE_ERROR,
                "Unable to download immutable Application source",
                details=details,
            ) from exc
        if revision.commit != reference.commit:
            raise DownloadError(
                ErrorCode.CATALOG_INVALID,
                "Downloaded source does not match the requested commit",
                details=details,
            )

        sink.emit(
            ProgressEvent(
                stage="validating_snapshot",
                message="Validating immutable Application source",
                data=details,
            )
        )
        _validate_snapshot_tree(isolated_target)
        application = check_application(
            isolated_target,
            watcherobot_version=watcherobot_version,
        )
        expected_space_name = f"WatcherRobot-{application.app_id}"
        if reference.space_id.split("/", 1)[1] != expected_space_name:
            raise DownloadError(
                ErrorCode.CATALOG_INVALID,
                "Downloaded Application ID does not match the Space",
                details={**details, "id": application.app_id},
            )

        sink.emit(
            ProgressEvent(
                stage="delivering_snapshot",
                message="Delivering Application to caller staging",
                data=details,
            )
        )
        try:
            shutil.copytree(
                isolated_target,
                destination,
                dirs_exist_ok=True,
                copy_function=shutil.copy2,
                ignore=_ignore_hub_local_metadata,
            )
        except OSError as exc:
            _clear_directory(destination)
            raise DownloadError(
                ErrorCode.INTERNAL_ERROR,
                "Unable to write to the caller-provided Application staging",
                details=details,
            ) from exc
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)

    return DownloadResult(
        space_id=reference.space_id,
        commit=reference.commit,
        source_url=revision.url,
        target=destination,
        application=application,
    )


def _require_empty_target(target: Path) -> Path:
    requested = Path(target)
    if requested.is_symlink() or not requested.is_dir():
        raise DownloadError(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            "Application target must be an existing empty caller-owned directory",
        )
    try:
        if any(requested.iterdir()):
            raise DownloadError(
                ErrorCode.APP_CONTENT_FORBIDDEN,
                "Application target directory must be empty",
            )
    except OSError as exc:
        raise DownloadError(
            ErrorCode.APP_CONTENT_FORBIDDEN,
            "Unable to read the Application target directory",
        ) from exc
    return requested.resolve()


def _validate_snapshot_tree(root: Path) -> None:
    files = 0
    total_bytes = 0
    for candidate in root.rglob("*"):
        if _is_hub_local_metadata(candidate, root):
            continue
        if candidate.is_symlink():
            raise ApplicationSourceError(
                "Downloaded Application must not contain symbolic links"
            )
        if not candidate.is_file():
            continue
        files += 1
        try:
            total_bytes += candidate.stat().st_size
        except OSError as exc:
            raise ApplicationSourceError(
                "Downloaded Application file cannot be inspected"
            ) from exc
        if files > MAX_SNAPSHOT_FILES:
            raise ApplicationSourceError(
                "Downloaded Application contains too many files"
            )
        if total_bytes > MAX_SNAPSHOT_BYTES:
            raise ApplicationSourceError(
                "Downloaded Application is too large"
            )


def _ignore_hub_local_metadata(directory: str, names: list[str]) -> set[str]:
    if Path(directory).resolve().name == ".cache" and "huggingface" in names:
        return {"huggingface"}
    return set()


def _is_hub_local_metadata(candidate: Path, root: Path) -> bool:
    relative = candidate.relative_to(root)
    return relative.parts[:2] == (".cache", "huggingface")


def _clear_directory(directory: Path) -> None:
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
