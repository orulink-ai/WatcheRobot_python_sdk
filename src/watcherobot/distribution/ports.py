"""Replaceable boundaries used by Application distribution services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


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


class HubError(RuntimeError):
    """Base failure raised by an authenticated Hub adapter."""


class HubAuthenticationError(HubError):
    """The stored access token is invalid or expired."""


class HubNetworkError(HubError):
    """The Hub could not be reached or returned a server failure."""


class HubInvalidResponse(HubError):
    """The Hub response does not satisfy the expected identity contract."""


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
