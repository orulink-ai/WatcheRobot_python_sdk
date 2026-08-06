"""Hugging Face OAuth Device Flow HTTP adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .ports import (
    AccessToken,
    DeviceAuthorization,
    OAuthAuthorizationDenied,
    OAuthAuthorizationExpired,
    OAuthAuthorizationPending,
    OAuthInvalidResponse,
    OAuthNetworkError,
    OAuthRequest,
    OAuthSlowDown,
)


DEVICE_ENDPOINT = "https://huggingface.co/oauth/device"
TOKEN_ENDPOINT = "https://huggingface.co/oauth/token"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_POLL_INTERVAL = 5
DEFAULT_HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class FormResponse:
    """HTTP status plus a payload hidden from standard representations."""

    status: int
    payload: dict[str, object] = field(repr=False)


class FormTransport(Protocol):
    """Injectable application/x-www-form-urlencoded transport."""

    def post_form(
        self,
        url: str,
        form: dict[str, str],
        *,
        timeout: float,
    ) -> FormResponse: ...


class UrllibFormTransport:
    """Standard-library HTTPS form transport with sanitized failures."""

    def post_form(
        self,
        url: str,
        form: dict[str, str],
        *,
        timeout: float,
    ) -> FormResponse:
        request = Request(
            url,
            data=urlencode(form).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return FormResponse(
                    status=response.status,
                    payload=_decode_payload(response.read()),
                )
        except HTTPError as exc:
            try:
                payload = _decode_payload(exc.read())
            except OAuthInvalidResponse:
                payload = {}
            return FormResponse(status=exc.code, payload=payload)
        except (OSError, TimeoutError, URLError) as exc:
            raise OAuthNetworkError(
                "Hugging Face OAuth request failed"
            ) from exc


class HuggingFaceOAuthClient:
    """Typed adapter for Hugging Face's public Device Code endpoints."""

    def __init__(
        self,
        *,
        transport: FormTransport | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self._transport = transport or UrllibFormTransport()
        self._timeout = timeout

    def request_device_authorization(
        self,
        request: OAuthRequest,
    ) -> DeviceAuthorization:
        response = self._transport.post_form(
            DEVICE_ENDPOINT,
            {
                "client_id": request.client_id,
                "scope": " ".join(request.scopes),
            },
            timeout=self._timeout,
        )
        if response.status < 200 or response.status >= 300:
            raise OAuthInvalidResponse(
                "Hugging Face rejected the device authorization request"
            )
        return DeviceAuthorization(
            device_code=_required_string(response.payload, "device_code"),
            user_code=_required_string(response.payload, "user_code"),
            verification_uri=_required_string(
                response.payload,
                "verification_uri",
            ),
            expires_in=_positive_integer(response.payload, "expires_in"),
            interval=_positive_integer(
                response.payload,
                "interval",
                default=DEFAULT_POLL_INTERVAL,
            ),
        )

    def poll_device_token(
        self,
        request: OAuthRequest,
        authorization: DeviceAuthorization,
    ) -> AccessToken:
        response = self._transport.post_form(
            TOKEN_ENDPOINT,
            {
                "grant_type": DEVICE_CODE_GRANT,
                "device_code": authorization.device_code,
                "client_id": request.client_id,
            },
            timeout=self._timeout,
        )
        if response.status < 200 or response.status >= 300:
            _raise_token_error(response.payload)

        token_type = response.payload.get("token_type")
        if token_type is not None and (
            not isinstance(token_type, str)
            or token_type.casefold() != "bearer"
        ):
            raise OAuthInvalidResponse(
                "Hugging Face returned an unsupported OAuth token type"
            )
        _validate_granted_scopes(response.payload, request.scopes)
        return AccessToken(
            _required_string(response.payload, "access_token")
        )


def _decode_payload(raw: bytes) -> dict[str, object]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthInvalidResponse(
            "Hugging Face returned invalid OAuth JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise OAuthInvalidResponse(
            "Hugging Face returned an invalid OAuth object"
        )
    return payload


def _required_string(payload: dict[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise OAuthInvalidResponse(
            f"Hugging Face OAuth response is missing {field_name}"
        )
    return value.strip()


def _positive_integer(
    payload: dict[str, object],
    field_name: str,
    *,
    default: int | None = None,
) -> int:
    value = payload.get(field_name, default)
    if type(value) is not int or value <= 0:
        raise OAuthInvalidResponse(
            f"Hugging Face OAuth response has invalid {field_name}"
        )
    return value


def _raise_token_error(payload: dict[str, object]) -> None:
    error = payload.get("error")
    error_types: dict[str, type[Exception]] = {
        "authorization_pending": OAuthAuthorizationPending,
        "slow_down": OAuthSlowDown,
        "access_denied": OAuthAuthorizationDenied,
        "expired_token": OAuthAuthorizationExpired,
    }
    if isinstance(error, str) and error in error_types:
        raise error_types[error]()
    raise OAuthInvalidResponse(
        "Hugging Face returned an unexpected OAuth token error"
    )


def _validate_granted_scopes(
    payload: dict[str, object],
    requested_scopes: tuple[str, ...],
) -> None:
    value = payload.get("scope")
    if value is None:
        return
    if not isinstance(value, str):
        raise OAuthInvalidResponse(
            "Hugging Face returned invalid OAuth scopes"
        )
    if set(value.split()) != set(requested_scopes):
        raise OAuthInvalidResponse(
            "Hugging Face granted unexpected OAuth scopes"
        )
