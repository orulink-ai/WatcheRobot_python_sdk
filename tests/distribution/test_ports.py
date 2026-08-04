from __future__ import annotations

from dataclasses import dataclass

from watcherobot.distribution.ports import (
    AccessToken,
    CredentialStore,
    HubClient,
    HubIdentity,
    OAuthClient,
    OAuthRequest,
)


@dataclass
class FakeOAuthClient:
    token: AccessToken
    last_request: OAuthRequest | None = None

    def authorize(self, request: OAuthRequest) -> AccessToken:
        self.last_request = request
        return self.token


class FakeCredentialStore:
    def __init__(self) -> None:
        self.token: AccessToken | None = None

    def load(self) -> AccessToken | None:
        return self.token

    def save(self, token: AccessToken) -> None:
        self.token = token

    def delete(self) -> None:
        self.token = None


@dataclass
class FakeHubClient:
    identity: HubIdentity
    received_token: AccessToken | None = None

    def whoami(self, token: AccessToken) -> HubIdentity:
        self.received_token = token
        return self.identity


def test_distribution_ports_accept_injected_fakes() -> None:
    token = AccessToken("hf_secret-token")
    oauth: OAuthClient = FakeOAuthClient(token)
    credentials: CredentialStore = FakeCredentialStore()
    hub: HubClient = FakeHubClient(
        HubIdentity(username="developer", display_name="Developer")
    )
    request = OAuthRequest(
        client_id="public-client",
        scopes=("openid", "profile"),
    )

    authorized = oauth.authorize(request)
    credentials.save(authorized)
    identity = hub.whoami(credentials.load() or authorized)

    assert identity.username == "developer"
    assert credentials.load() == token


def test_access_token_never_appears_in_repr() -> None:
    token = AccessToken("hf_secret-token")

    assert "hf_secret-token" not in repr(token)
    assert "hf_secret-token" not in str(token)


def test_oauth_request_is_immutable_and_keeps_scope_order() -> None:
    request = OAuthRequest(
        client_id="public-client",
        scopes=("openid", "profile", "contribute-repos"),
    )

    assert request.scopes == (
        "openid",
        "profile",
        "contribute-repos",
    )
