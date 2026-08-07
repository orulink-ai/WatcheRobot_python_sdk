from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from watcherobot.distribution.login import DEFAULT_OAUTH_REQUEST
from watcherobot.distribution.oauth_http import (
    DEVICE_ENDPOINT,
    TOKEN_ENDPOINT,
    FormResponse,
    HuggingFaceOAuthClient,
)
from watcherobot.distribution.ports import (
    OAuthAuthorizationDenied,
    OAuthAuthorizationExpired,
    OAuthAuthorizationPending,
    OAuthInvalidResponse,
    OAuthSlowDown,
)


@dataclass
class FakeTransport:
    responses: list[FormResponse]
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def post_form(
        self,
        url: str,
        form: dict[str, str],
        *,
        timeout: float,
    ) -> FormResponse:
        self.requests.append((url, dict(form)))
        return self.responses.pop(0)


def test_oauth_client_requests_device_code_with_exact_public_scopes() -> None:
    transport = FakeTransport(
        [
            FormResponse(
                status=200,
                payload={
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://hf.co/oauth/device",
                    "expires_in": 300,
                },
            )
        ]
    )
    client = HuggingFaceOAuthClient(transport=transport)

    authorization = client.request_device_authorization(DEFAULT_OAUTH_REQUEST)

    assert transport.requests == [
        (
            DEVICE_ENDPOINT,
            {
                "client_id": "65c05ae4-072e-425b-98e9-06aa89bab970",
                "scope": (
                    "openid profile contribute-repos write-discussions"
                ),
            },
        )
    ]
    assert authorization.user_code == "ABCD-EFGH"
    assert authorization.verification_uri == "https://hf.co/oauth/device"
    assert authorization.expires_in == 300
    assert authorization.interval == 5
    assert "secret-device-code" not in repr(authorization)


def test_oauth_client_uses_provider_poll_interval_when_present() -> None:
    transport = FakeTransport(
        [
            FormResponse(
                status=200,
                payload={
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://hf.co/oauth/device",
                    "expires_in": 300,
                    "interval": 7,
                },
            )
        ]
    )

    authorization = HuggingFaceOAuthClient(
        transport=transport
    ).request_device_authorization(DEFAULT_OAUTH_REQUEST)

    assert authorization.interval == 7


@pytest.mark.parametrize(
    ("provider_error", "expected_exception"),
    [
        ("authorization_pending", OAuthAuthorizationPending),
        ("slow_down", OAuthSlowDown),
        ("access_denied", OAuthAuthorizationDenied),
        ("expired_token", OAuthAuthorizationExpired),
    ],
)
def test_oauth_client_maps_token_polling_errors(
    provider_error: str,
    expected_exception: type[BaseException],
) -> None:
    transport = FakeTransport(
        [
            _device_response(),
            FormResponse(
                status=400,
                payload={
                    "error": provider_error,
                    "error_description": "provider detail",
                },
            ),
        ]
    )
    client = HuggingFaceOAuthClient(transport=transport)
    authorization = client.request_device_authorization(DEFAULT_OAUTH_REQUEST)

    with pytest.raises(expected_exception):
        client.poll_device_token(DEFAULT_OAUTH_REQUEST, authorization)

    token_request = transport.requests[-1]
    assert token_request[0] == TOKEN_ENDPOINT
    assert token_request[1] == {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": "secret-device-code",
        "client_id": "65c05ae4-072e-425b-98e9-06aa89bab970",
    }


def test_oauth_client_returns_redacted_token_and_validates_scopes() -> None:
    transport = FakeTransport(
        [
            _device_response(),
            FormResponse(
                status=200,
                payload={
                    "access_token": "hf_secret-token",
                    "token_type": "bearer",
                    "scope": (
                        "openid profile contribute-repos write-discussions"
                    ),
                },
            ),
        ]
    )
    client = HuggingFaceOAuthClient(transport=transport)
    authorization = client.request_device_authorization(DEFAULT_OAUTH_REQUEST)

    token = client.poll_device_token(DEFAULT_OAUTH_REQUEST, authorization)

    assert token.value == "hf_secret-token"
    assert "hf_secret-token" not in repr(token)


@pytest.mark.parametrize(
    "response",
    [
        FormResponse(status=200, payload={}),
        FormResponse(
            status=200,
            payload={
                "device_code": "secret-device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://hf.co/oauth/device",
                "expires_in": 0,
            },
        ),
        FormResponse(
            status=500,
            payload={"error": "server_error"},
        ),
    ],
)
def test_oauth_client_rejects_invalid_device_responses(
    response: FormResponse,
) -> None:
    client = HuggingFaceOAuthClient(transport=FakeTransport([response]))

    with pytest.raises(OAuthInvalidResponse) as captured:
        client.request_device_authorization(DEFAULT_OAUTH_REQUEST)

    assert "secret-device-code" not in str(captured.value)


def test_oauth_client_rejects_unexpected_granted_scope() -> None:
    transport = FakeTransport(
        [
            _device_response(),
            FormResponse(
                status=200,
                payload={
                    "access_token": "hf_secret-token",
                    "scope": "openid profile write-repos",
                },
            ),
        ]
    )
    client = HuggingFaceOAuthClient(transport=transport)
    authorization = client.request_device_authorization(DEFAULT_OAUTH_REQUEST)

    with pytest.raises(OAuthInvalidResponse) as captured:
        client.poll_device_token(DEFAULT_OAUTH_REQUEST, authorization)

    assert "hf_secret-token" not in str(captured.value)


def _device_response() -> FormResponse:
    return FormResponse(
        status=200,
        payload={
            "device_code": "secret-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://hf.co/oauth/device",
            "expires_in": 300,
        },
    )
