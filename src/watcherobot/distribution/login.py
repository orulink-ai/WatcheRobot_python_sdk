"""OAuth Device Flow orchestration independent from HTTP and keyring details."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .credentials import CredentialStoreError
from .events import ErrorCode, EventSink, ProgressEvent
from .ports import (
    AccessToken,
    CredentialStore,
    DeviceAuthorization,
    HubAuthenticationError,
    HubClient,
    HubInvalidResponse,
    HubIdentity,
    HubNetworkError,
    OAuthAuthorizationDenied,
    OAuthAuthorizationExpired,
    OAuthAuthorizationPending,
    OAuthClient,
    OAuthInvalidResponse,
    OAuthNetworkError,
    OAuthRequest,
    OAuthSlowDown,
)


OAUTH_CLIENT_ID = "65c05ae4-072e-425b-98e9-06aa89bab970"
OAUTH_SCOPES = (
    "openid",
    "profile",
    "contribute-repos",
    "write-discussions",
)
DEFAULT_OAUTH_REQUEST = OAuthRequest(
    client_id=OAUTH_CLIENT_ID,
    scopes=OAUTH_SCOPES,
)
_SLOW_DOWN_SECONDS = 5
_MAX_NETWORK_ATTEMPTS = 3
_NETWORK_RETRY_DELAY_SECONDS = 1


class LoginError(RuntimeError):
    """Sanitized login failure with a stable machine code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class LoginResult:
    """Non-sensitive identity returned after credential persistence."""

    username: str
    display_name: str = ""
    reused: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "reused": self.reused,
        }


@dataclass(frozen=True)
class LoginStatus:
    """Non-sensitive state of the Watcher-specific OAuth credential."""

    logged_in: bool
    username: str = ""
    display_name: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"logged_in": self.logged_in}
        if self.logged_in:
            payload.update(
                {
                    "username": self.username,
                    "display_name": self.display_name,
                }
            )
        return payload


def login(
    *,
    oauth: OAuthClient,
    credentials: CredentialStore,
    hub: HubClient,
    events: EventSink,
    request: OAuthRequest = DEFAULT_OAUTH_REQUEST,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    cancelled: Callable[[], bool] = lambda: False,
    force: bool = False,
) -> LoginResult:
    """Authorize, verify the HF identity, then persist the credential."""

    if not force:
        status = login_status(
            credentials=credentials,
            hub=hub,
            sleep=sleep,
        )
        if status.logged_in:
            return LoginResult(
                username=status.username,
                display_name=status.display_name,
                reused=True,
            )

    try:
        authorization = _request_device_authorization(
            oauth,
            request,
            sleep=sleep,
        )
    except OAuthNetworkError as exc:
        raise LoginError(
            ErrorCode.AUTH_NETWORK_ERROR,
            "无法连接 Hugging Face 登录服务",
        ) from exc
    except OAuthInvalidResponse as exc:
        raise LoginError(
            ErrorCode.AUTH_INVALID_RESPONSE,
            "Hugging Face 登录服务返回了无效响应",
        ) from exc

    events.emit(
        ProgressEvent(
            stage="authorization_required",
            message="请在浏览器中授权 Hugging Face 登录",
            data={
                "verification_uri": authorization.verification_uri,
                "user_code": authorization.user_code,
                "expires_in": authorization.expires_in,
            },
        )
    )

    deadline = monotonic() + authorization.expires_in
    interval = authorization.interval
    consecutive_network_failures = 0
    while True:
        if cancelled():
            raise LoginError(
                ErrorCode.OPERATION_CANCELLED,
                "Hugging Face 登录已取消",
            )
        if monotonic() >= deadline:
            raise LoginError(
                ErrorCode.AUTH_EXPIRED,
                "Hugging Face 登录授权已过期",
            )
        try:
            token = oauth.poll_device_token(request, authorization)
            break
        except OAuthAuthorizationPending:
            consecutive_network_failures = 0
        except OAuthSlowDown:
            consecutive_network_failures = 0
            interval += _SLOW_DOWN_SECONDS
        except OAuthAuthorizationDenied as exc:
            raise LoginError(
                ErrorCode.AUTH_DENIED,
                "Hugging Face 登录授权被拒绝",
            ) from exc
        except OAuthAuthorizationExpired as exc:
            raise LoginError(
                ErrorCode.AUTH_EXPIRED,
                "Hugging Face 登录授权已过期",
            ) from exc
        except OAuthNetworkError as exc:
            consecutive_network_failures += 1
            if consecutive_network_failures >= _MAX_NETWORK_ATTEMPTS:
                raise LoginError(
                    ErrorCode.AUTH_NETWORK_ERROR,
                    "无法连接 Hugging Face 登录服务",
                ) from exc
        except OAuthInvalidResponse as exc:
            raise LoginError(
                ErrorCode.AUTH_INVALID_RESPONSE,
                "Hugging Face 登录服务返回了无效响应",
            ) from exc
        sleep(min(interval, max(0.0, deadline - monotonic())))

    identity = _verify_identity(hub, token, sleep=sleep)
    try:
        credentials.save(token)
    except CredentialStoreError as exc:
        raise LoginError(
            ErrorCode.CREDENTIAL_STORE_ERROR,
            "无法保存 Watcher Hugging Face 系统凭据",
        ) from exc
    return LoginResult(
        username=identity.username,
        display_name=identity.display_name,
    )


def login_status(
    *,
    credentials: CredentialStore,
    hub: HubClient,
    sleep: Callable[[float], None] = time.sleep,
) -> LoginStatus:
    """Verify the saved Watcher credential without starting OAuth."""

    try:
        token = credentials.load()
    except CredentialStoreError as exc:
        raise LoginError(
            ErrorCode.CREDENTIAL_STORE_ERROR,
            "无法读取 Watcher Hugging Face 系统凭据",
        ) from exc
    if token is None:
        return LoginStatus(logged_in=False)

    try:
        identity = _whoami_with_network_retry(hub, token, sleep=sleep)
    except HubAuthenticationError:
        _delete_credential(credentials)
        return LoginStatus(logged_in=False)
    except HubNetworkError as exc:
        raise LoginError(
            ErrorCode.AUTH_NETWORK_ERROR,
            "无法连接 Hugging Face 身份服务",
        ) from exc
    except HubInvalidResponse as exc:
        raise LoginError(
            ErrorCode.AUTH_INVALID_RESPONSE,
            "Hugging Face 身份服务返回了无效响应",
        ) from exc
    return LoginStatus(
        logged_in=True,
        username=identity.username,
        display_name=identity.display_name,
    )


def logout(*, credentials: CredentialStore) -> LoginStatus:
    """Delete only the Watcher-specific credential entry."""

    _delete_credential(credentials)
    return LoginStatus(logged_in=False)


def _verify_identity(
    hub: HubClient,
    token: AccessToken,
    *,
    sleep: Callable[[float], None],
) -> HubIdentity:
    try:
        return _whoami_with_network_retry(hub, token, sleep=sleep)
    except HubAuthenticationError as exc:
        raise LoginError(
            ErrorCode.AUTH_INVALID_RESPONSE,
            "Hugging Face 登录身份验证失败",
        ) from exc
    except HubNetworkError as exc:
        raise LoginError(
            ErrorCode.AUTH_NETWORK_ERROR,
            "无法连接 Hugging Face 身份服务",
        ) from exc
    except HubInvalidResponse as exc:
        raise LoginError(
            ErrorCode.AUTH_INVALID_RESPONSE,
            "Hugging Face 身份服务返回了无效响应",
        ) from exc


def _delete_credential(credentials: CredentialStore) -> None:
    try:
        credentials.delete()
    except CredentialStoreError as exc:
        raise LoginError(
            ErrorCode.CREDENTIAL_STORE_ERROR,
            "无法删除 Watcher Hugging Face 系统凭据",
        ) from exc


def _request_device_authorization(
    oauth: OAuthClient,
    request: OAuthRequest,
    *,
    sleep: Callable[[float], None],
) -> DeviceAuthorization:
    for attempt in range(1, _MAX_NETWORK_ATTEMPTS + 1):
        try:
            return oauth.request_device_authorization(request)
        except OAuthNetworkError:
            if attempt >= _MAX_NETWORK_ATTEMPTS:
                raise
            sleep(_NETWORK_RETRY_DELAY_SECONDS)
    raise AssertionError("network retry loop must return or raise")


def _whoami_with_network_retry(
    hub: HubClient,
    token: AccessToken,
    *,
    sleep: Callable[[float], None],
) -> HubIdentity:
    for attempt in range(1, _MAX_NETWORK_ATTEMPTS + 1):
        try:
            return hub.whoami(token)
        except HubNetworkError:
            if attempt >= _MAX_NETWORK_ATTEMPTS:
                raise
            sleep(_NETWORK_RETRY_DELAY_SECONDS)
    raise AssertionError("network retry loop must return or raise")
