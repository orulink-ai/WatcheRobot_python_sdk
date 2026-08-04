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
class HubIdentity:
    """Non-sensitive Hugging Face account information."""

    username: str
    display_name: str = ""


class OAuthClient(Protocol):
    """Obtain an access token without prescribing a concrete OAuth UI."""

    def authorize(self, request: OAuthRequest) -> AccessToken: ...


class CredentialStore(Protocol):
    """Persist only the Watcher distribution tool's OAuth credential."""

    def load(self) -> AccessToken | None: ...

    def save(self, token: AccessToken) -> None: ...

    def delete(self) -> None: ...


class HubClient(Protocol):
    """Authenticated Hugging Face operations used by distribution services."""

    def whoami(self, token: AccessToken) -> HubIdentity: ...
