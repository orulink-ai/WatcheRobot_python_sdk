from __future__ import annotations

import json
from types import SimpleNamespace

from watcherobot.cli import main
from watcherobot.distribution.ports import (
    AccessToken,
    DeviceAuthorization,
    HubIdentity,
    OAuthNetworkError,
)


class FakeCredentials:
    def __init__(self, token: AccessToken | None = None) -> None:
        self.token = token
        self.delete_calls = 0

    def load(self) -> AccessToken | None:
        return self.token

    def save(self, token: AccessToken) -> None:
        self.token = token

    def delete(self) -> None:
        self.delete_calls += 1
        self.token = None


class FakeOAuth:
    def __init__(self, outcome: AccessToken | BaseException) -> None:
        self.outcome = outcome
        self.authorization_requests = 0

    def request_device_authorization(self, request):
        self.authorization_requests += 1
        if isinstance(self.outcome, OAuthNetworkError):
            raise self.outcome
        return DeviceAuthorization(
            device_code="private-device-code",
            user_code="ABCD-EFGH",
            verification_uri="https://hf.co/oauth/device",
            expires_in=300,
            interval=5,
        )

    def poll_device_token(self, request, authorization) -> AccessToken:
        assert isinstance(self.outcome, AccessToken)
        return self.outcome


class FakeHub:
    def whoami(self, token: AccessToken) -> HubIdentity:
        return HubIdentity(username="developer", display_name="Developer")


def _install_fakes(monkeypatch, *, token: AccessToken | None = None):
    credentials = FakeCredentials(token)
    oauth = FakeOAuth(AccessToken("hf_private-token"))
    dependencies = SimpleNamespace(
        oauth=oauth,
        credentials=credentials,
        hub=FakeHub(),
    )
    monkeypatch.setattr(
        "watcherobot.cli._build_auth_dependencies",
        lambda: dependencies,
        raising=False,
    )

    def fail_if_called():
        raise AssertionError("authentication commands must not start the Daemon")

    monkeypatch.setattr("watcherobot.cli.ensure_runtime", fail_if_called)
    return oauth, credentials


def _json_lines(output: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.splitlines()]


def test_cli_app_login_jsonl_emits_instructions_and_never_starts_daemon(
    monkeypatch,
    capsys,
) -> None:
    _install_fakes(monkeypatch)

    exit_code = main(["app", "login", "--jsonl"])

    captured = capsys.readouterr()
    events = _json_lines(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert events == [
        {
            "type": "progress",
            "stage": "authorization_required",
            "message": "请在浏览器中授权 Hugging Face 登录",
            "data": {
                "verification_uri": "https://hf.co/oauth/device",
                "user_code": "ABCD-EFGH",
                "expires_in": 300,
            },
        },
        {
            "type": "result",
            "ok": True,
            "data": {
                "username": "developer",
                "display_name": "Developer",
                "reused": False,
            },
        },
    ]
    assert "hf_private-token" not in captured.out
    assert "private-device-code" not in captured.out


def test_cli_app_login_status_jsonl_reuses_watcher_credential(
    monkeypatch,
    capsys,
) -> None:
    oauth, _credentials = _install_fakes(
        monkeypatch,
        token=AccessToken("hf_stored-token"),
    )

    exit_code = main(["app", "login", "--status", "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert _json_lines(captured.out) == [
        {
            "type": "result",
            "ok": True,
            "data": {
                "logged_in": True,
                "username": "developer",
                "display_name": "Developer",
            },
        }
    ]
    assert oauth.authorization_requests == 0


def test_cli_app_logout_jsonl_deletes_exact_watcher_credential(
    monkeypatch,
    capsys,
) -> None:
    _oauth, credentials = _install_fakes(
        monkeypatch,
        token=AccessToken("hf_stored-token"),
    )

    exit_code = main(["app", "logout", "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert _json_lines(captured.out) == [
        {
            "type": "result",
            "ok": True,
            "data": {"logged_in": False},
        }
    ]
    assert credentials.delete_calls == 1


def test_cli_app_login_jsonl_maps_sanitized_remote_error(
    monkeypatch,
    capsys,
) -> None:
    dependencies = SimpleNamespace(
        oauth=FakeOAuth(OAuthNetworkError("leaked hf_private-token")),
        credentials=FakeCredentials(),
        hub=FakeHub(),
    )
    monkeypatch.setattr(
        "watcherobot.cli._build_auth_dependencies",
        lambda: dependencies,
        raising=False,
    )

    exit_code = main(["app", "login", "--jsonl"])

    captured = capsys.readouterr()
    assert exit_code == 4
    assert captured.err == ""
    assert _json_lines(captured.out) == [
        {
            "type": "error",
            "ok": False,
            "code": "auth_network_error",
            "message": "无法连接 Hugging Face 登录服务",
        }
    ]
    assert "hf_private-token" not in captured.out


def test_cli_app_login_human_output_shows_authorization_and_identity(
    monkeypatch,
    capsys,
) -> None:
    _install_fakes(monkeypatch)

    exit_code = main(["app", "login"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "https://hf.co/oauth/device" in captured.out
    assert "ABCD-EFGH" in captured.out
    assert "developer" in captured.out
    assert "hf_private-token" not in captured.out
