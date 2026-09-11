"""Publish Application source without changing the official catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .check import check_application
from .credentials import CredentialStoreError
from .events import ErrorCode, EventSink, ProgressEvent
from .login import LoginError, login_status
from .providers import get_provider
from .ports import (
    AccessToken,
    CredentialStore,
    HubAuthenticationError,
    HubClient,
    HubError,
    HubRepositoryConflict,
    PublishHubClient,
)
from .source_files import collect_application_source_files
from .ports import UploadFile
from .publish_files import prepare_space_upload_files


SPACE_SDK = "static"


class PublishError(RuntimeError):
    """Sanitized source-publication failure with a stable machine contract."""

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
class PublishResult:
    """Public immutable source revision after one successful upload."""

    repo_id: str
    commit: str
    repository_url: str
    source_url: str

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_id": self.repo_id,
            "commit": self.commit,
            "repository_url": self.repository_url,
            "source_url": self.source_url,
        }


def publish_application(
    application_dir: Path,
    *,
    provider: str,
    credentials: CredentialStore,
    identity_hub: HubClient,
    publish_hub: PublishHubClient,
    events: EventSink,
    watcherobot_version: str | None = None,
) -> PublishResult:
    """Validate and upload one Application source snapshot."""

    get_provider(provider)
    events.emit(
        ProgressEvent(stage="checking", message="Validating Application")
    )
    application = check_application(
        application_dir,
        watcherobot_version=watcherobot_version,
    )
    files = prepare_space_upload_files(application_dir, application) if provider == "huggingface" else tuple(
        UploadFile.from_path(p.as_posix(), Path(application_dir).resolve() / p)
        for p in collect_application_source_files(Path(application_dir).resolve())
    )

    events.emit(
        ProgressEvent(
            stage="authenticating",
            message="Verifying selected provider login",
        )
    )
    identity, token = _load_verified_identity(
        credentials=credentials,
        identity_hub=identity_hub,
    )
    repo_id = f"{identity.username}/WatcherRobot-{application.app_id}"
    repository_url = get_provider(provider).repository_url(repo_id)

    events.emit(
        ProgressEvent(
            stage="ensuring_space",
            message="Creating or verifying the public source repository",
            data={"repo_id": repo_id},
        )
    )
    try:
        publish_hub.ensure_public_repository(
            token,
            repo_id=repo_id,
            sdk=SPACE_SDK,
        )
    except HubRepositoryConflict as exc:
        raise PublishError(
            ErrorCode.SPACE_OWNERSHIP_CONFLICT,
            "The existing source repository does not meet publication ownership or visibility requirements",
            details={"repo_id": repo_id},
        ) from exc
    except HubError as exc:
        raise _remote_error("Unable to create or verify the source repository", exc)

    events.emit(
        ProgressEvent(
            stage="uploading_source",
            message="Uploading Application source",
            data={"repo_id": repo_id},
        )
    )
    try:
        publish_hub.replace_repository_files(
            token,
            repo_id=repo_id,
            files=files,
            commit_message=f"Publish {application.app_id} {application.version}",
        )
    except HubRepositoryConflict as exc:
        raise PublishError(
            ErrorCode.SPACE_OWNERSHIP_CONFLICT,
            "The existing source repository does not meet publication ownership or visibility requirements",
            details={"repo_id": repo_id},
        ) from exc
    except HubError as exc:
        raise _remote_error("Unable to upload Application source", exc)

    events.emit(
        ProgressEvent(
            stage="resolving_commit",
            message="Resolving the immutable source commit",
            data={"repo_id": repo_id},
        )
    )
    try:
        revision = publish_hub.get_repository_head(token, repo_id=repo_id)
    except HubError as exc:
        raise _remote_error("Unable to resolve the complete Space commit", exc)

    return PublishResult(
        repo_id=repo_id,
        commit=revision.commit,
        repository_url=repository_url,
        source_url=revision.url,
    )


@dataclass(frozen=True)
class _VerifiedIdentity:
    username: str
    display_name: str = ""


def _load_verified_identity(
    *,
    credentials: CredentialStore,
    identity_hub: HubClient,
) -> tuple[_VerifiedIdentity, AccessToken]:
    try:
        status = login_status(credentials=credentials, hub=identity_hub)
    except LoginError as exc:
        raise PublishError(exc.code, str(exc)) from exc
    if not status.logged_in:
        raise PublishError(
            ErrorCode.AUTH_REQUIRED,
            "Sign in to the selected provider before publishing an Application",
        )
    try:
        token = credentials.load()
    except CredentialStoreError as exc:
        raise PublishError(
            ErrorCode.CREDENTIAL_STORE_ERROR,
            "Unable to read the selected provider credential",
        ) from exc
    if token is None:
        raise PublishError(
            ErrorCode.AUTH_REQUIRED,
            "The selected provider credential is missing; sign in again",
        )
    return (
        _VerifiedIdentity(
            username=status.username,
            display_name=status.display_name,
        ),
        token,
    )


def _remote_error(message: str, error: Exception) -> PublishError:
    code = (
        ErrorCode.AUTH_REQUIRED
        if isinstance(error, HubAuthenticationError)
        else ErrorCode.REMOTE_ERROR
    )
    return PublishError(code, message)
