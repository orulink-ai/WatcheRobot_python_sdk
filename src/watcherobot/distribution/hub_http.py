"""Minimal authenticated Hugging Face Hub HTTP adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ports import (
    AccessToken,
    HubAuthenticationError,
    HubIdentity,
    HubInvalidResponse,
    HubNetworkError,
)


WHOAMI_ENDPOINT = "https://huggingface.co/api/whoami-v2"
DEFAULT_HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class JsonResponse:
    """HTTP status plus a JSON payload hidden from repr."""

    status: int
    payload: dict[str, object] = field(repr=False)


class JsonTransport(Protocol):
    """Injectable authenticated JSON GET transport."""

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: float,
    ) -> JsonResponse: ...


class UrllibJsonTransport:
    """Standard-library HTTPS transport with sanitized errors."""

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: float,
    ) -> JsonResponse:
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                return JsonResponse(
                    status=response.status,
                    payload=_decode_payload(response.read()),
                )
        except HTTPError as exc:
            try:
                payload = _decode_payload(exc.read())
            except HubInvalidResponse:
                payload = {}
            return JsonResponse(status=exc.code, payload=payload)
        except (OSError, TimeoutError, URLError) as exc:
            raise HubNetworkError(
                "Hugging Face Hub request failed"
            ) from exc


class HuggingFaceHubClient:
    """Hub identity adapter used before persisting OAuth credentials."""

    def __init__(
        self,
        *,
        transport: JsonTransport | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self._transport = transport or UrllibJsonTransport()
        self._timeout = timeout

    def whoami(self, token: AccessToken) -> HubIdentity:
        response = self._transport.get_json(
            WHOAMI_ENDPOINT,
            {
                "Authorization": f"Bearer {token.value}",
                "Accept": "application/json",
            },
            timeout=self._timeout,
        )
        if response.status in {401, 403}:
            raise HubAuthenticationError(
                "Hugging Face access token is invalid or expired"
            )
        if response.status < 200 or response.status >= 300:
            raise HubNetworkError("Hugging Face Hub is unavailable")

        username = response.payload.get("name")
        display_name = response.payload.get("fullname", "")
        if not isinstance(username, str) or not username.strip():
            raise HubInvalidResponse(
                "Hugging Face identity is missing the username"
            )
        if not isinstance(display_name, str):
            raise HubInvalidResponse(
                "Hugging Face identity has an invalid display name"
            )
        return HubIdentity(
            username=username.strip(),
            display_name=display_name.strip(),
        )


def _decode_payload(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HubInvalidResponse(
            "Hugging Face Hub returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise HubInvalidResponse(
            "Hugging Face Hub returned an invalid object"
        )
    return payload
