"""Watcher-specific OAuth credential storage backed by the operating system."""

from __future__ import annotations

from typing import Protocol

import keyring

from .ports import AccessToken


CREDENTIAL_SERVICE = "ai.orulink.watcher-desktop.huggingface"
MODELSCOPE_CREDENTIAL_SERVICE = "ai.orulink.watcher-desktop.modelscope"
CREDENTIAL_ACCOUNT = "oauth-access-token"


class KeyringBackend(Protocol):
    """Subset of keyring used by the Watcher credential adapter."""

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(
        self,
        service: str,
        username: str,
        password: str,
    ) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class CredentialStoreError(RuntimeError):
    """Sanitized operating-system credential failure."""

    code = "credential_store_error"


class SystemCredentialStore:
    """Store exactly one Watcher OAuth token in the platform keyring."""

    def __init__(
        self,
        *,
        provider: str = "huggingface",
        backend: KeyringBackend = keyring,
    ) -> None:
        services = {
            "huggingface": CREDENTIAL_SERVICE,
            "modelscope": MODELSCOPE_CREDENTIAL_SERVICE,
        }
        if provider not in services:
            raise ValueError(f"unsupported credential provider: {provider}")
        self._provider = provider
        self._service = services[provider]
        self._backend = backend

    def load(self) -> AccessToken | None:
        try:
            value = self._backend.get_password(
                self._service,
                CREDENTIAL_ACCOUNT,
            )
        except Exception as exc:
            raise CredentialStoreError(
                f"Unable to read the Watcher {self._provider} credential"
            ) from exc
        if value is None:
            return None
        try:
            return AccessToken(value)
        except ValueError as exc:
            raise CredentialStoreError(
                f"The Watcher {self._provider} credential is invalid"
            ) from exc

    def save(self, token: AccessToken) -> None:
        try:
            self._backend.set_password(
                self._service,
                CREDENTIAL_ACCOUNT,
                token.value,
            )
        except Exception as exc:
            raise CredentialStoreError(
                f"Unable to save the Watcher {self._provider} credential"
            ) from exc

    def delete(self) -> None:
        if self.load() is None:
            return
        try:
            self._backend.delete_password(
                self._service,
                CREDENTIAL_ACCOUNT,
            )
        except Exception as exc:
            raise CredentialStoreError(
                f"Unable to delete the Watcher {self._provider} credential"
            ) from exc
