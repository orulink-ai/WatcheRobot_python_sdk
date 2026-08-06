from __future__ import annotations

from dataclasses import dataclass

from watcherobot.distribution.ports import (
    AccessToken,
    CredentialStore,
    DeviceAuthorization,
    HubClient,
    HubIdentity,
    OAuthClient,
    OAuthRequest,
)


@dataclass
class FakeOAuthClient:
    token: AccessToken
    last_request: OAuthRequest | None = None

    def request_device_authorization(
        self,
        request: OAuthRequest,
    ) -> DeviceAuthorization:
        self.last_request = request
        return DeviceAuthorization(
            device_code="secret-device-code",
            user_code="ABCD-EFGH",
            verification_uri="https://hf.co/oauth/device",
            expires_in=300,
            interval=5,
        )

    def poll_device_token(
        self,
        request: OAuthRequest,
        authorization: DeviceAuthorization,
    ) -> AccessToken:
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

    device = oauth.request_device_authorization(request)
    authorized = oauth.poll_device_token(request, device)
    credentials.save(authorized)
    identity = hub.whoami(credentials.load() or authorized)

    assert identity.username == "developer"
    assert credentials.load() == token


def test_access_token_never_appears_in_repr() -> None:
    token = AccessToken("hf_secret-token")

    assert "hf_secret-token" not in repr(token)
    assert "hf_secret-token" not in str(token)


def test_device_code_never_appears_in_repr() -> None:
    authorization = DeviceAuthorization(
        device_code="secret-device-code",
        user_code="ABCD-EFGH",
        verification_uri="https://hf.co/oauth/device",
        expires_in=300,
        interval=5,
    )

    assert "secret-device-code" not in repr(authorization)


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
