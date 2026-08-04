"""Replaceable boundaries used by Application distribution services."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol


_FULL_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class AccessToken:
    """Secret OAuth token whose standard representations are always redacted."""

    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("access token must not be empty")


@dataclass(frozen=True)
class OAuthRequest:
    """Public OAuth client configuration for one authorization request."""

    client_id: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class DeviceAuthorization:
    """Public instructions plus a redacted device-flow credential."""

    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int

    def __post_init__(self) -> None:
        if not self.device_code:
            raise ValueError("device code must not be empty")
        if self.expires_in <= 0:
            raise ValueError("device authorization expiry must be positive")
        if self.interval <= 0:
            raise ValueError("device authorization interval must be positive")


@dataclass(frozen=True)
class HubIdentity:
    """Non-sensitive Hugging Face account information."""

    username: str
    display_name: str = ""


@dataclass(frozen=True)
class UploadFile:
    """One exact file in a Space snapshot without exposing local content."""

    path_in_repo: str
    source_path: Path | None = field(default=None, repr=False)
    content: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        normalized = PurePosixPath(self.path_in_repo)
        if (
            not self.path_in_repo
            or "\\" in self.path_in_repo
            or normalized.is_absolute()
            or ".." in normalized.parts
            or normalized.as_posix() != self.path_in_repo
        ):
            raise ValueError("upload path must be a normalized relative POSIX path")
        if (self.source_path is None) == (self.content is None):
            raise ValueError("upload file must contain exactly one source")

    @classmethod
    def from_path(cls, path_in_repo: str, source_path: Path) -> UploadFile:
        return cls(path_in_repo=path_in_repo, source_path=Path(source_path))

    @classmethod
    def from_bytes(cls, path_in_repo: str, content: bytes) -> UploadFile:
        return cls(path_in_repo=path_in_repo, content=bytes(content))


@dataclass(frozen=True)
class SpaceRepository:
    """Result of ensuring one public source Space exists."""

    space_id: str
    created: bool


@dataclass(frozen=True)
class RepositoryRevision:
    """One immutable Hub revision and its fixed source-tree URL."""

    commit: str
    url: str

    def __post_init__(self) -> None:
        _validate_full_commit(self.commit)
        if not self.url:
            raise ValueError("repository revision URL must not be empty")


@dataclass(frozen=True)
class CatalogDocument:
    """Catalog bytes read together with the exact parent revision."""

    content: bytes = field(repr=False)
    commit: str

    def __post_init__(self) -> None:
        _validate_full_commit(self.commit)


@dataclass(frozen=True)
class CatalogPullRequest:
    """Non-sensitive metadata for one catalog pull request."""

    number: int
    title: str
    url: str
    status: str

    def __post_init__(self) -> None:
        if self.number <= 0:
            raise ValueError("pull request number must be positive")
        if not self.title or not self.url or not self.status:
            raise ValueError("pull request metadata must not be empty")


class OAuthClient(Protocol):
    """Perform the two HTTP operations of OAuth Device Code flow."""

    def request_device_authorization(
        self,
        request: OAuthRequest,
    ) -> DeviceAuthorization: ...

    def poll_device_token(
        self,
        request: OAuthRequest,
        authorization: DeviceAuthorization,
    ) -> AccessToken: ...


class CredentialStore(Protocol):
    """Persist only the Watcher distribution tool's OAuth credential."""

    def load(self) -> AccessToken | None: ...

    def save(self, token: AccessToken) -> None: ...

    def delete(self) -> None: ...


class HubClient(Protocol):
    """Authenticated Hugging Face operations used by distribution services."""

    def whoami(self, token: AccessToken) -> HubIdentity: ...


class PublishHubClient(Protocol):
    """Remote writes and authenticated reads for publish/submit commands."""

    def ensure_public_space(
        self,
        token: AccessToken,
        *,
        space_id: str,
        sdk: str,
    ) -> SpaceRepository: ...

    def replace_space_files(
        self,
        token: AccessToken,
        *,
        space_id: str,
        files: tuple[UploadFile, ...],
        commit_message: str,
    ) -> None: ...

    def get_space_head(
        self,
        token: AccessToken,
        *,
        space_id: str,
    ) -> RepositoryRevision: ...

    def read_space_file(
        self,
        token: AccessToken,
        *,
        space_id: str,
        commit: str,
        path: str,
    ) -> bytes: ...

    def read_catalog(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        path: str,
    ) -> CatalogDocument: ...

    def list_open_catalog_pull_requests(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        author: str,
    ) -> tuple[CatalogPullRequest, ...]: ...

    def create_catalog_pull_request(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        path: str,
        content: bytes,
        parent_commit: str,
        title: str,
        description: str,
    ) -> CatalogPullRequest: ...


class MarketplaceHubClient(Protocol):
    """Unauthenticated public reads required by the official marketplace."""

    def read_public_catalog(
        self,
        *,
        repo_id: str,
        path: str,
    ) -> CatalogDocument: ...

    def read_space_file(
        self,
        *,
        space_id: str,
        commit: str,
        path: str,
    ) -> bytes: ...

    def download_space_snapshot(
        self,
        *,
        space_id: str,
        commit: str,
        target: Path,
    ) -> RepositoryRevision: ...


class HubError(RuntimeError):
    """Base failure raised by an authenticated Hub adapter."""


class HubAuthenticationError(HubError):
    """The stored access token is invalid or expired."""


class HubNetworkError(HubError):
    """The Hub could not be reached or returned a server failure."""


class HubInvalidResponse(HubError):
    """The Hub response does not satisfy the expected identity contract."""


class HubRepositoryConflict(HubError):
    """A target repository exists but this OAuth App cannot manage it."""


class HubCatalogConflict(HubError):
    """The catalog changed before its pull request could be created."""


class HubRepositoryNotFound(HubError):
    """A required public Hugging Face repository does not exist."""


class HubRevisionNotFound(HubError):
    """A required immutable repository revision does not exist."""


class HubFileNotFound(HubError):
    """A required file does not exist at the requested revision."""


class OAuthFlowError(RuntimeError):
    """Base error raised by an OAuthClient implementation."""


class OAuthAuthorizationPending(OAuthFlowError):
    """The user has not completed authorization yet."""


class OAuthSlowDown(OAuthFlowError):
    """The provider requests a slower polling interval."""


class OAuthAuthorizationDenied(OAuthFlowError):
    """The user rejected the authorization request."""


class OAuthAuthorizationExpired(OAuthFlowError):
    """The device authorization is no longer valid."""


class OAuthNetworkError(OAuthFlowError):
    """The OAuth provider could not be reached or returned invalid transport."""


class OAuthInvalidResponse(OAuthFlowError):
    """The OAuth provider response does not satisfy the expected contract."""


def _validate_full_commit(commit: str) -> None:
    if _FULL_COMMIT_PATTERN.fullmatch(commit) is None:
        raise ValueError("commit must be a lowercase 40-character SHA")
