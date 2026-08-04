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
    PublishHubClient,
)
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

    space_id: str
    commit: str
    space_url: str
    source_url: str

    def to_dict(self) -> dict[str, object]:
        return {
            "space_id": self.space_id,
            "commit": self.commit,
            "space_url": self.space_url,
            "source_url": self.source_url,
        }


def publish_application(
    application_dir: Path,
    *,
    credentials: CredentialStore,
    identity_hub: HubClient,
    publish_hub: PublishHubClient,
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
            message="Verifying Hugging Face login",
        )
    )
    identity, token = _load_verified_identity(
        credentials=credentials,
        identity_hub=identity_hub,
    )
    space_id = f"{identity.username}/WatcherRobot-{application.app_id}"
    space_url = f"https://huggingface.co/spaces/{space_id}"

    events.emit(
        ProgressEvent(
            stage="ensuring_space",
            message="Creating or verifying the public source Space",
            data={"space_id": space_id},
        )
    )
    try:
        publish_hub.ensure_public_space(
            token,
            space_id=space_id,
            sdk=SPACE_SDK,
        )
    except HubRepositoryConflict as exc:
        raise PublishError(
            ErrorCode.SPACE_OWNERSHIP_CONFLICT,
            "The existing Space is not owned by the Watcher publishing tool",
            details={"space_id": space_id},
        ) from exc
    except HubError as exc:
        raise _remote_error("Unable to create or verify the Hugging Face Space", exc)

    events.emit(
        ProgressEvent(
            stage="uploading_source",
            message="Uploading Application source",
            data={"space_id": space_id},
        )
    )
    try:
        publish_hub.replace_space_files(
            token,
            space_id=space_id,
            files=files,
            commit_message=f"Publish {application.app_id} {application.version}",
        )
    except HubRepositoryConflict as exc:
        raise PublishError(
            ErrorCode.SPACE_OWNERSHIP_CONFLICT,
            "The existing Space is not owned by the Watcher publishing tool",
            details={"space_id": space_id},
        ) from exc
    except HubError as exc:
        raise _remote_error("Unable to upload Application source", exc)

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
        raise _remote_error("Unable to resolve the complete Space commit", exc)

    return PublishResult(
        space_id=space_id,
        commit=revision.commit,
        space_url=space_url,
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
            "Sign in to Hugging Face before publishing an Application",
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
