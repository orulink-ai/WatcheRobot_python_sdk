"""Publish Application source without changing the official catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .check import check_application
from .credentials import CredentialStoreError
from .events import ErrorCode, EventSink, ProgressEvent
from .login import LoginError, login_status
from .ports import (
    AccessToken,
    CredentialStore,
    HubAuthenticationError,
    HubClient,
    HubError,
    HubRepositoryConflict,
    SourcePublishClient,
)
from .publish_files import prepare_space_upload_files


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

    provider: str
    repository_id: str
    repository_type: str
    commit: str
    repository_url: str
    source_url: str

    @property
    def space_id(self) -> str:
        """Backward-compatible HF identifier used by the current submit flow."""
        return self.repository_id

    @property
    def space_url(self) -> str:
        """Backward-compatible HF URL used by existing callers."""
        return self.repository_url

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "provider": self.provider,
            "repository_id": self.repository_id,
            "repository_type": self.repository_type,
            "commit": self.commit,
            "repository_url": self.repository_url,
            "source_url": self.source_url,
        }
        if self.provider == "huggingface":
            # Keep the original machine contract for existing Desktop clients.
            payload["space_id"] = self.repository_id
            payload["space_url"] = self.repository_url
        return payload


def publish_application(
    application_dir: Path,
    *,
    credentials: CredentialStore,
    identity_hub: HubClient,
    publish_hub: SourcePublishClient,
    events: EventSink,
    watcherobot_version: str | None = None,
) -> PublishResult:
    """Validate and upload one Application source snapshot."""

    events.emit(
        ProgressEvent(stage="checking", message="Validating Application")
    )
    application = check_application(
        application_dir,
        watcherobot_version=watcherobot_version,
    )
    files = prepare_space_upload_files(application_dir, application)

    events.emit(
        ProgressEvent(
            stage="authenticating",
            message=f"Verifying {publish_hub.display_name} login",
        )
    )
    identity, token = _load_verified_identity(
        credentials=credentials,
        identity_hub=identity_hub,
        provider_display_name=publish_hub.display_name,
    )
    repository_id = f"{identity.username}/WatcherRobot-{application.app_id}"
    repository_url = publish_hub.repository_url(repository_id)

    events.emit(
        ProgressEvent(
            stage="ensuring_repository",
            message=(
                f"Creating or verifying the public {publish_hub.display_name} "
                "source repository"
            ),
            data={"provider": publish_hub.provider, "repository_id": repository_id},
        )
    )
    try:
        publish_hub.ensure_public_repository(
            token,
            repository_id=repository_id,
        )
    except HubRepositoryConflict as exc:
        raise PublishError(
            ErrorCode.SPACE_OWNERSHIP_CONFLICT,
            "The existing repository is not owned by the Watcher publishing tool",
            details={"provider": publish_hub.provider, "repository_id": repository_id},
        ) from exc
    except HubError as exc:
        raise _remote_error(
            f"Unable to create or verify the {publish_hub.display_name} repository",
            exc,
        )

    events.emit(
        ProgressEvent(
            stage="uploading_source",
            message="Uploading Application source",
            data={"provider": publish_hub.provider, "repository_id": repository_id},
        )
    )
    try:
        publish_hub.replace_repository_files(
            token,
            repository_id=repository_id,
            files=files,
            commit_message=f"Publish {application.app_id} {application.version}",
        )
    except HubRepositoryConflict as exc:
        raise PublishError(
            ErrorCode.SPACE_OWNERSHIP_CONFLICT,
            "The existing repository is not owned by the Watcher publishing tool",
            details={"provider": publish_hub.provider, "repository_id": repository_id},
        ) from exc
    except HubError as exc:
        raise _remote_error("Unable to upload Application source", exc)

    events.emit(
        ProgressEvent(
            stage="resolving_commit",
            message="Resolving the immutable source commit",
            data={"provider": publish_hub.provider, "repository_id": repository_id},
        )
    )
    try:
        revision = publish_hub.get_repository_head(
            token,
            repository_id=repository_id,
        )
    except HubError as exc:
        raise _remote_error("Unable to resolve the complete repository commit", exc)

    return PublishResult(
        provider=publish_hub.provider,
        repository_id=repository_id,
        repository_type=publish_hub.repository_type,
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
    provider_display_name: str = "Hugging Face",
) -> tuple[_VerifiedIdentity, AccessToken]:
    try:
        status = login_status(
            credentials=credentials,
            hub=identity_hub,
            provider_display_name=provider_display_name,
        )
    except LoginError as exc:
        raise PublishError(exc.code, str(exc)) from exc
    if not status.logged_in:
        raise PublishError(
            ErrorCode.AUTH_REQUIRED,
            f"Sign in to {provider_display_name} before publishing an Application",
        )
    try:
        token = credentials.load()
    except CredentialStoreError as exc:
        raise PublishError(
            ErrorCode.CREDENTIAL_STORE_ERROR,
            "Unable to read the Watcher Hugging Face credential",
        ) from exc
    if token is None:
        raise PublishError(
            ErrorCode.AUTH_REQUIRED,
            "The Hugging Face credential is missing; sign in again",
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
