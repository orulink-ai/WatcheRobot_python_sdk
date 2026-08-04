from __future__ import annotations

import pytest

from watcherobot.distribution.credentials import (
    CREDENTIAL_ACCOUNT,
    CREDENTIAL_SERVICE,
    CredentialStoreError,
    SystemCredentialStore,
)
from watcherobot.distribution.ports import AccessToken


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[object, ...]] = []
        self.failure: BaseException | None = None

    def get_password(self, service: str, username: str) -> str | None:
        self.calls.append(("get", service, username))
        if self.failure is not None:
            raise self.failure
        return self.values.get((service, username))

    def set_password(
        self,
        service: str,
        username: str,
        password: str,
    ) -> None:
        self.calls.append(("set", service, username, "<redacted>"))
        if self.failure is not None:
            raise self.failure
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.calls.append(("delete", service, username))
        if self.failure is not None:
            raise self.failure
        del self.values[(service, username)]


def test_system_credential_store_uses_one_watcher_specific_entry() -> None:
    backend = FakeKeyring()
    store = SystemCredentialStore(backend=backend)
    token = AccessToken("hf_secret-token")

    assert store.load() is None
    store.save(token)
    loaded = store.load()
    store.delete()
    store.delete()

    assert loaded == token
    assert backend.values == {}
    assert backend.calls == [
        ("get", CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT),
        ("set", CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT, "<redacted>"),
        ("get", CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT),
        ("get", CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT),
        ("delete", CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT),
        ("get", CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT),
    ]


@pytest.mark.parametrize("operation", ["load", "save", "delete"])
def test_system_credential_store_sanitizes_backend_failures(
    operation: str,
) -> None:
    backend = FakeKeyring()
    backend.values[(CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT)] = (
        "hf_secret-token"
    )
    backend.failure = RuntimeError("backend leaked hf_secret-token")
    store = SystemCredentialStore(backend=backend)

    with pytest.raises(CredentialStoreError) as captured:
        if operation == "load":
            store.load()
        elif operation == "save":
            store.save(AccessToken("hf_secret-token"))
        else:
            store.delete()

    assert captured.value.code == "credential_store_error"
    assert "hf_secret-token" not in str(captured.value)
