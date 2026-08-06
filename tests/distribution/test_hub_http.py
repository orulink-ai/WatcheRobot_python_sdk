from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from watcherobot.distribution.hub_http import (
    WHOAMI_ENDPOINT,
    HuggingFaceHubClient,
    JsonResponse,
)
from watcherobot.distribution.ports import (
    AccessToken,
    HubAuthenticationError,
    HubInvalidResponse,
    HubNetworkError,
)


@dataclass
class FakeTransport:
    responses: list[JsonResponse]
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: float,
    ) -> JsonResponse:
        self.requests.append((url, dict(headers)))
        return self.responses.pop(0)


def test_hub_client_verifies_identity_with_bearer_token() -> None:
    transport = FakeTransport(
        [
            JsonResponse(
                status=200,
                payload={
                    "type": "user",
                    "name": "developer",
                    "fullname": "Demo Developer",
                },
            )
        ]
    )
    client = HuggingFaceHubClient(transport=transport)
    token = AccessToken("hf_secret-token")

    identity = client.whoami(token)

    assert identity.username == "developer"
    assert identity.display_name == "Demo Developer"
    assert transport.requests == [
        (
            WHOAMI_ENDPOINT,
            {
                "Authorization": "Bearer hf_secret-token",
                "Accept": "application/json",
            },
        )
    ]
    assert "hf_secret-token" not in repr(identity)


@pytest.mark.parametrize("status", [401, 403])
def test_hub_client_maps_invalid_or_expired_token(status: int) -> None:
    client = HuggingFaceHubClient(
        transport=FakeTransport(
            [JsonResponse(status=status, payload={"error": "unauthorized"})]
        )
    )

    with pytest.raises(HubAuthenticationError) as captured:
        client.whoami(AccessToken("hf_secret-token"))

    assert "hf_secret-token" not in str(captured.value)


def test_hub_client_maps_server_failure_without_payload_leak() -> None:
    client = HuggingFaceHubClient(
        transport=FakeTransport(
            [
                JsonResponse(
                    status=503,
                    payload={"error": "leaked hf_secret-token"},
                )
            ]
        )
    )

    with pytest.raises(HubNetworkError) as captured:
        client.whoami(AccessToken("hf_secret-token"))

    assert "hf_secret-token" not in str(captured.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": 7},
        {"name": "developer", "fullname": 7},
    ],
)
def test_hub_client_rejects_invalid_identity_payload(
    payload: dict[str, object],
) -> None:
    client = HuggingFaceHubClient(
        transport=FakeTransport([JsonResponse(status=200, payload=payload)])
    )

    with pytest.raises(HubInvalidResponse):
        client.whoami(AccessToken("hf_secret-token"))
