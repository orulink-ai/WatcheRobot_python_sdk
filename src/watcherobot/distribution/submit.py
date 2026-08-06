"""Submit an already-published Application revision to the official catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
    ApplicationManifestMetadata,
    parse_application_manifest,
)

from .catalog_submission import (
    CATALOG_PATH,
    CATALOG_REPO_ID,
    CatalogDocumentError,
    CatalogPullRequestConflict,
    catalog_pull_request_title,
    plan_catalog_submission,
)
from .check import ApplicationCheckResult, check_application
from .events import ErrorCode, EventSink, ProgressEvent
from .ports import (
    CredentialStore,
    HubAuthenticationError,
    HubCatalogConflict,
    HubClient,
    HubError,
    PublishHubClient,
    RepositoryRevision,
)
from .publish import PublishError, _load_verified_identity


_SUBMIT_REQUIRED_METADATA = ("description", "author")


class SubmitError(RuntimeError):
    """Sanitized catalog-submission failure with a stable machine contract."""

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
class SubmitResult:
    """Catalog state for one immutable published Application revision."""

    space_id: str
    commit: str
    source_url: str
    pr_url: str
    pr_status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "space_id": self.space_id,
            "commit": self.commit,
            "source_url": self.source_url,
            "pr_url": self.pr_url,
            "pr_status": self.pr_status,
        }


def submit_application(
    application_dir: Path,
    *,
    commit: str | None,
    credentials: CredentialStore,
    identity_hub: HubClient,
    publish_hub: PublishHubClient,
    events: EventSink,
    watcherobot_version: str | None = None,
) -> SubmitResult:
    """Validate a published snapshot and submit only its catalog reference."""

    events.emit(
        ProgressEvent(stage="checking", message="Validating Application")
    )
    local_application = check_application(
        application_dir,
        watcherobot_version=watcherobot_version,
    )
    _validate_submission_metadata(local_application)

    events.emit(
        ProgressEvent(
            stage="authenticating",
            message="Verifying Hugging Face login",
        )
    )
    try:
        identity, token = _load_verified_identity(
            credentials=credentials,
            identity_hub=identity_hub,
        )
    except PublishError as exc:
        raise SubmitError(exc.code, str(exc), details=exc.details) from exc

    space_id = f"{identity.username}/WatcherRobot-{local_application.app_id}"
    if commit is None:
        events.emit(
            ProgressEvent(
                stage="resolving_commit",
                message="Resolving the immutable source commit",
                data={"space_id": space_id},
            )
        )
        try:
            revision = publish_hub.get_space_head(token, space_id=space_id)
        except HubError as exc:
            raise _remote_error(
                "Unable to resolve the published Space commit",
                exc,
                details={"space_id": space_id},
            )
    else:
        try:
            revision = RepositoryRevision(
                commit=commit,
                url=f"https://huggingface.co/spaces/{space_id}/tree/{commit}",
            )
        except ValueError as exc:
            raise SubmitError(
                ErrorCode.APP_MANIFEST_INVALID,
                "Catalog submission commit must be a full lowercase 40-character SHA",
                details={"space_id": space_id},
            ) from exc

    source_details: dict[str, object] = {
        "space_id": space_id,
        "commit": revision.commit,
        "source_url": revision.url,
    }
    events.emit(
        ProgressEvent(
            stage="verifying_source",
            message="Verifying the published Application snapshot",
            data=source_details,
        )
    )
    try:
        manifest_document = publish_hub.read_space_file(
            token,
            space_id=space_id,
            commit=revision.commit,
            path="app.json",
        )
    except HubError as exc:
        raise _remote_error(
            "Unable to read app.json from the published commit",
            exc,
            details=source_details,
        )
    try:
        remote_application = parse_application_manifest(
            manifest_document,
            watcherobot_version=watcherobot_version,
        )
    except ApplicationManifestError as exc:
        raise SubmitError(
            ErrorCode.APP_MANIFEST_INVALID,
            "The published commit contains an invalid app.json",
            details=source_details,
        ) from exc
    _validate_submission_metadata(remote_application)
    if local_application.to_dict() != remote_application.to_dict():
        raise SubmitError(
            ErrorCode.APP_MANIFEST_INVALID,
            "Local app.json does not match the published commit; publish the "
            "current project or check out the matching source before submitting",
            details=source_details,
        )
    if remote_application.icon:
        try:
            publish_hub.read_space_file(
                token,
                space_id=space_id,
                commit=revision.commit,
                path=remote_application.icon,
            )
        except HubError as exc:
            raise _remote_error(
                "Unable to read the Application icon from the published commit",
                exc,
                details=source_details,
            )

    events.emit(
        ProgressEvent(
            stage="updating_catalog",
            message="Preparing the official marketplace submission",
            data={"space_id": space_id, "commit": revision.commit},
        )
    )
    try:
        catalog = publish_hub.read_catalog(
            token,
            repo_id=CATALOG_REPO_ID,
            path=CATALOG_PATH,
        )
        open_pull_requests = publish_hub.list_open_catalog_pull_requests(
            token,
            repo_id=CATALOG_REPO_ID,
            author=identity.username,
        )
        plan = plan_catalog_submission(
            catalog,
            open_pull_requests=open_pull_requests,
            space_id=space_id,
            commit=revision.commit,
        )
    except CatalogDocumentError as exc:
        raise SubmitError(
            ErrorCode.CATALOG_INVALID,
            "The official Application marketplace is invalid; submission stopped",
            details=source_details,
        ) from exc
    except CatalogPullRequestConflict as exc:
        raise SubmitError(
            ErrorCode.CATALOG_PR_CONFLICT,
            "This Application already has a pending marketplace PR for "
            "another commit",
            details={**source_details, "pr_url": exc.pull_request.url},
        ) from exc
    except HubError as exc:
        raise _remote_error(
            "Unable to read the official marketplace or pull request status",
            exc,
            details=source_details,
        )

    if plan.status == "already_listed":
        return SubmitResult(
            space_id=space_id,
            commit=revision.commit,
            source_url=revision.url,
            pr_url="",
            pr_status="already_listed",
        )
    if plan.status == "pending":
        assert plan.pull_request is not None
        return SubmitResult(
            space_id=space_id,
            commit=revision.commit,
            source_url=revision.url,
            pr_url=plan.pull_request.url,
            pr_status="pending",
        )

    assert plan.content is not None
    try:
        pull_request = publish_hub.create_catalog_pull_request(
            token,
            repo_id=CATALOG_REPO_ID,
            path=CATALOG_PATH,
            content=plan.content,
            parent_commit=plan.parent_commit,
            title=catalog_pull_request_title(space_id, revision.commit),
            description=_catalog_pull_request_description(
                remote_application,
                space_id=space_id,
                commit=revision.commit,
                source_url=revision.url,
            ),
        )
    except HubCatalogConflict as exc:
        raise SubmitError(
            ErrorCode.CATALOG_PR_CONFLICT,
            "The official marketplace changed; submit again to create a new PR",
            details=source_details,
        ) from exc
    except HubError as exc:
        raise _remote_error(
            "Unable to create the official marketplace pull request",
            exc,
            details=source_details,
        )
    return SubmitResult(
        space_id=space_id,
        commit=revision.commit,
        source_url=revision.url,
        pr_url=pull_request.url,
        pr_status="pending",
    )


def _validate_submission_metadata(
    application: ApplicationCheckResult | ApplicationManifestMetadata,
) -> None:
    missing = [
        field
        for field in _SUBMIT_REQUIRED_METADATA
        if not getattr(application, field)
    ]
    if missing:
        raise ApplicationManifestError(
            "Catalog submission requires non-empty app.json fields: "
            + ", ".join(missing)
        )


def _catalog_pull_request_description(
    application: ApplicationManifestMetadata,
    *,
    space_id: str,
    commit: str,
    source_url: str,
) -> str:
    icon_preview = ""
    icon_value = "Default WatcherRobot icon"
    if application.icon:
        icon_url = (
            f"https://huggingface.co/spaces/{space_id}/resolve/{commit}/"
            f"{quote(application.icon, safe='/')}"
        )
        icon_preview = f"![Application icon]({icon_url})\n\n"
        icon_value = f"`{_markdown_table_text(application.icon)}`"
    dependencies = (
        ", ".join(f"`{dependency}`" for dependency in application.dependencies)
        or "None"
    )
    rows = (
        ("Name", _markdown_table_text(application.name)),
        ("Application ID", f"`{application.app_id}`"),
        ("Version", f"`{application.version}`"),
        ("Author", _markdown_table_text(application.author)),
        ("Description", _markdown_table_text(application.description)),
        ("SDK requirement", f"`{application.requires_watcherobot}`"),
        ("Dependencies", dependencies),
        ("Icon", icon_value),
    )
    table = "\n".join(
        ["| Field | Value |", "| --- | --- |"]
        + [f"| {label} | {value} |" for label, value in rows]
    )
    return (
        "# Application review\n\n"
        f"{icon_preview}"
        f"{table}\n\n"
        f"[View fixed source]({source_url})\n\n"
        f"Space: `{space_id}`  \n"
        f"Commit: `{commit}`"
    )


def _markdown_table_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def _remote_error(
    message: str,
    error: Exception,
    *,
    details: dict[str, object] | None = None,
) -> SubmitError:
    code = (
        ErrorCode.AUTH_REQUIRED
        if isinstance(error, HubAuthenticationError)
        else ErrorCode.REMOTE_ERROR
    )
    return SubmitError(code, message, details=details)
