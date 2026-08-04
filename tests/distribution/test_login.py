from __future__ import annotations

from dataclasses import dataclass

import pytest

from watcherobot.distribution.events import ErrorCode, ProgressEvent
from watcherobot.distribution.login import (
    DEFAULT_OAUTH_REQUEST,
    LoginError,
    login,
    login_status,
    logout,
)
from watcherobot.distribution.credentials import CredentialStoreError
from watcherobot.distribution.ports import (
    AccessToken,
    DeviceAuthorization,
    HubIdentity,
    HubAuthenticationError,
    HubNetworkError,
    OAuthAuthorizationDenied,
    OAuthAuthorizationExpired,
    OAuthAuthorizationPending,
    OAuthNetworkError,
    OAuthRequest,
    OAuthSlowDown,
)


class RecordingEvents:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event) -> None:
        self.events.append(event)


class FakeOAuthClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requests: list[OAuthRequest] = []
        self.authorization = DeviceAuthorization(
            device_code="secret-device-code",
            user_code="ABCD-EFGH",
            verification_uri="https://hf.co/oauth/device",
            expires_in=300,
            interval=5,
        )

    def request_device_authorization(
        self,
        request: OAuthRequest,
    ) -> DeviceAuthorization:
        self.requests.append(request)
        return self.authorization

    def poll_device_token(
        self,
        request: OAuthRequest,
        authorization: DeviceAuthorization,
    ) -> AccessToken:
        self.requests.append(request)
        assert authorization is self.authorization
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, AccessToken)
        return outcome


class FlakyDeviceOAuthClient(FakeOAuthClient):
    def __init__(self, outcomes: list[object], failures: int) -> None:
        super().__init__(outcomes)
        self.failures = failures
        self.authorization_attempts = 0

    def request_device_authorization(
        self,
        request: OAuthRequest,
    ) -> DeviceAuthorization:
        self.authorization_attempts += 1
        if self.authorization_attempts <= self.failures:
            raise OAuthNetworkError("temporary device endpoint failure")
        return super().request_device_authorization(request)


class FakeCredentialStore:
    def __init__(self) -> None:
        self.token: AccessToken | None = None
        self.delete_calls = 0

    def load(self) -> AccessToken | None:
        return self.token

    def save(self, token: AccessToken) -> None:
        self.token = token

    def delete(self) -> None:
        self.delete_calls += 1
        self.token = None


@dataclass
class FakeHubClient:
    identity: HubIdentity
    token: AccessToken | None = None

    def whoami(self, token: AccessToken) -> HubIdentity:
        self.token = token
        return self.identity


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_login_waits_for_authorization_verifies_identity_and_saves_token() -> None:
    token = AccessToken("hf_secret-token")
    oauth = FakeOAuthClient(
        [
            OAuthAuthorizationPending(),
            OAuthAuthorizationPending(),
            token,
        ]
    )
    credentials = FakeCredentialStore()
    hub = FakeHubClient(HubIdentity(username="developer"))
    events = RecordingEvents()
    clock = FakeClock()

    result = login(
        oauth=oauth,
        credentials=credentials,
        hub=hub,
        events=events,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.username == "developer"
    assert credentials.token is token
    assert hub.token is token
    assert oauth.requests == [DEFAULT_OAUTH_REQUEST] * 4
    assert clock.sleeps == [5, 5]
    assert events.events == [
        ProgressEvent(
            stage="authorization_required",
            message="请在浏览器中授权 Hugging Face 登录",
            data={
                "verification_uri": "https://hf.co/oauth/device",
                "user_code": "ABCD-EFGH",
                "expires_in": 300,
            },
        )
    ]
    assert "hf_secret-token" not in repr(result)
    assert "secret-device-code" not in repr(events.events)


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [
        (OAuthAuthorizationDenied(), ErrorCode.AUTH_DENIED),
        (OAuthAuthorizationExpired(), ErrorCode.AUTH_EXPIRED),
        (OAuthNetworkError("network unavailable"), ErrorCode.AUTH_NETWORK_ERROR),
    ],
)
def test_login_maps_oauth_failures_to_stable_sanitized_errors(
    outcome: BaseException,
    expected_code: ErrorCode,
) -> None:
    outcomes = [outcome] * 3 if isinstance(outcome, OAuthNetworkError) else [outcome]
    oauth = FakeOAuthClient(outcomes)

    with pytest.raises(LoginError) as captured:
        login(
            oauth=oauth,
            credentials=FakeCredentialStore(),
            hub=FakeHubClient(HubIdentity(username="developer")),
            events=RecordingEvents(),
            sleep=lambda _seconds: None,
        )

    assert captured.value.code is expected_code
    assert "secret-device-code" not in str(captured.value)


def test_login_expires_locally_when_authorization_never_completes() -> None:
    oauth = FakeOAuthClient([OAuthAuthorizationPending()] * 3)
    oauth.authorization = DeviceAuthorization(
        device_code="secret-device-code",
        user_code="ABCD-EFGH",
        verification_uri="https://hf.co/oauth/device",
        expires_in=10,
        interval=5,
    )
    clock = FakeClock()

    with pytest.raises(LoginError) as captured:
        login(
            oauth=oauth,
            credentials=FakeCredentialStore(),
            hub=FakeHubClient(HubIdentity(username="developer")),
            events=RecordingEvents(),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert captured.value.code is ErrorCode.AUTH_EXPIRED
    assert clock.sleeps == [5, 5]


def test_login_honors_provider_slow_down_without_busy_polling() -> None:
    token = AccessToken("hf_secret-token")
    oauth = FakeOAuthClient([OAuthSlowDown(), token])
    clock = FakeClock()

    result = login(
        oauth=oauth,
        credentials=FakeCredentialStore(),
        hub=FakeHubClient(HubIdentity(username="developer")),
        events=RecordingEvents(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.username == "developer"
    assert clock.sleeps == [10]


def test_login_cancellation_never_polls_or_saves_credentials() -> None:
    oauth = FakeOAuthClient([AccessToken("hf_secret-token")])
    credentials = FakeCredentialStore()

    with pytest.raises(LoginError) as captured:
        login(
            oauth=oauth,
            credentials=credentials,
            hub=FakeHubClient(HubIdentity(username="developer")),
            events=RecordingEvents(),
            cancelled=lambda: True,
        )

    assert captured.value.code is ErrorCode.OPERATION_CANCELLED
    assert oauth.outcomes == [AccessToken("hf_secret-token")]
    assert credentials.token is None


def test_login_reuses_verified_stored_token_without_starting_oauth() -> None:
    stored = AccessToken("hf_stored-token")
    credentials = FakeCredentialStore()
    credentials.token = stored
    oauth = FakeOAuthClient([])
    hub = FakeHubClient(HubIdentity(username="developer"))

    result = login(
        oauth=oauth,
        credentials=credentials,
        hub=hub,
        events=RecordingEvents(),
    )

    assert result.username == "developer"
    assert result.reused is True
    assert oauth.requests == []
    assert hub.token is stored


def test_login_force_ignores_valid_stored_token() -> None:
    credentials = FakeCredentialStore()
    credentials.token = AccessToken("hf_stored-token")
    replacement = AccessToken("hf_replacement-token")
    oauth = FakeOAuthClient([replacement])

    result = login(
        oauth=oauth,
        credentials=credentials,
        hub=FakeHubClient(HubIdentity(username="developer")),
        events=RecordingEvents(),
        force=True,
    )

    assert result.reused is False
    assert credentials.token is replacement
    assert oauth.requests == [DEFAULT_OAUTH_REQUEST] * 2


class FailingHubClient:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def whoami(self, token: AccessToken) -> HubIdentity:
        raise self.error


def test_login_status_clears_only_expired_watcher_credential() -> None:
    credentials = FakeCredentialStore()
    credentials.token = AccessToken("hf_expired-token")

    status = login_status(
        credentials=credentials,
        hub=FailingHubClient(HubAuthenticationError()),
    )

    assert status.to_dict() == {"logged_in": False}
    assert credentials.token is None
    assert credentials.delete_calls == 1


def test_login_status_preserves_token_on_temporary_network_failure() -> None:
    credentials = FakeCredentialStore()
    credentials.token = AccessToken("hf_stored-token")

    with pytest.raises(LoginError) as captured:
        login_status(
            credentials=credentials,
            hub=FailingHubClient(HubNetworkError()),
            sleep=lambda _seconds: None,
        )

    assert captured.value.code is ErrorCode.AUTH_NETWORK_ERROR
    assert credentials.token is not None
    assert credentials.delete_calls == 0


def test_login_status_reports_absent_and_verified_credentials() -> None:
    credentials = FakeCredentialStore()
    hub = FakeHubClient(
        HubIdentity(username="developer", display_name="Developer")
    )

    assert login_status(credentials=credentials, hub=hub).to_dict() == {
        "logged_in": False
    }
    credentials.token = AccessToken("hf_stored-token")
    assert login_status(credentials=credentials, hub=hub).to_dict() == {
        "logged_in": True,
        "username": "developer",
        "display_name": "Developer",
    }


def test_logout_deletes_only_injected_credential_store_entry() -> None:
    credentials = FakeCredentialStore()
    credentials.token = AccessToken("hf_stored-token")

    result = logout(credentials=credentials)

    assert result.to_dict() == {"logged_in": False}
    assert credentials.token is None
    assert credentials.delete_calls == 1


class FailingCredentialStore(FakeCredentialStore):
    def load(self) -> AccessToken | None:
        raise CredentialStoreError("backend leaked hf_secret-token")


def test_login_status_sanitizes_credential_backend_failure() -> None:
    with pytest.raises(LoginError) as captured:
        login_status(
            credentials=FailingCredentialStore(),
            hub=FakeHubClient(HubIdentity(username="developer")),
        )

    assert captured.value.code is ErrorCode.CREDENTIAL_STORE_ERROR
    assert "hf_secret-token" not in str(captured.value)


def test_login_retries_temporary_device_authorization_network_errors() -> None:
    token = AccessToken("hf_secret-token")
    oauth = FlakyDeviceOAuthClient([token], failures=2)
    credentials = FakeCredentialStore()
    clock = FakeClock()

    result = login(
        oauth=oauth,
        credentials=credentials,
        hub=FakeHubClient(HubIdentity(username="developer")),
        events=RecordingEvents(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.username == "developer"
    assert oauth.authorization_attempts == 3
    assert clock.sleeps == [1, 1]
    assert credentials.token is token


def test_login_retries_temporary_token_poll_network_errors() -> None:
    token = AccessToken("hf_secret-token")
    oauth = FakeOAuthClient(
        [
            OAuthNetworkError("temporary token endpoint failure"),
            OAuthNetworkError("temporary token endpoint failure"),
            token,
        ]
    )
    credentials = FakeCredentialStore()
    clock = FakeClock()

    result = login(
        oauth=oauth,
        credentials=credentials,
        hub=FakeHubClient(HubIdentity(username="developer")),
        events=RecordingEvents(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.username == "developer"
    assert oauth.requests == [DEFAULT_OAUTH_REQUEST] * 4
    assert clock.sleeps == [5, 5]
    assert credentials.token is token


def test_login_stops_after_three_consecutive_token_network_errors() -> None:
    oauth = FakeOAuthClient(
        [OAuthNetworkError("temporary token endpoint failure")] * 3
    )
    credentials = FakeCredentialStore()
    clock = FakeClock()

    with pytest.raises(LoginError) as captured:
        login(
            oauth=oauth,
            credentials=credentials,
            hub=FakeHubClient(HubIdentity(username="developer")),
            events=RecordingEvents(),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )

    assert captured.value.code is ErrorCode.AUTH_NETWORK_ERROR
    assert oauth.requests == [DEFAULT_OAUTH_REQUEST] * 4
    assert clock.sleeps == [5, 5]
    assert credentials.token is None


class FlakyHubClient:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    def whoami(self, token: AccessToken) -> HubIdentity:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise HubNetworkError("temporary identity endpoint failure")
        return HubIdentity(username="developer", display_name="Developer")


def test_login_retries_temporary_identity_network_errors() -> None:
    token = AccessToken("hf_secret-token")
    hub = FlakyHubClient(failures=2)
    credentials = FakeCredentialStore()
    clock = FakeClock()

    result = login(
        oauth=FakeOAuthClient([token]),
        credentials=credentials,
        hub=hub,
        events=RecordingEvents(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result.username == "developer"
    assert hub.attempts == 3
    assert clock.sleeps == [1, 1]
    assert credentials.token is token


def test_login_status_retries_identity_network_errors_without_deleting_token() -> None:
    credentials = FakeCredentialStore()
    credentials.token = AccessToken("hf_stored-token")
    hub = FlakyHubClient(failures=2)
    clock = FakeClock()

    status = login_status(
        credentials=credentials,
        hub=hub,
        sleep=clock.sleep,
    )

    assert status.logged_in is True
    assert hub.attempts == 3
    assert clock.sleeps == [1, 1]
    assert credentials.token is not None
