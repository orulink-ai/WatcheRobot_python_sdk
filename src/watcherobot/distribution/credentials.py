"""Watcher-specific OAuth credential storage backed by the operating system."""

from __future__ import annotations

from typing import Protocol

import keyring

from .ports import AccessToken


CREDENTIAL_SERVICE = "ai.orulink.watcher-desktop.huggingface"
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

    def __init__(self, *, backend: KeyringBackend = keyring) -> None:
        self._backend = backend

    def load(self) -> AccessToken | None:
        try:
            value = self._backend.get_password(
                CREDENTIAL_SERVICE,
                CREDENTIAL_ACCOUNT,
            )
        except Exception as exc:
            raise CredentialStoreError(
                "无法读取 Watcher Hugging Face 系统凭据"
            ) from exc
        if value is None:
            return None
        try:
            return AccessToken(value)
        except ValueError as exc:
            raise CredentialStoreError(
                "Watcher Hugging Face 系统凭据无效"
            ) from exc

    def save(self, token: AccessToken) -> None:
        try:
            self._backend.set_password(
                CREDENTIAL_SERVICE,
                CREDENTIAL_ACCOUNT,
                token.value,
            )
        except Exception as exc:
            raise CredentialStoreError(
                "无法保存 Watcher Hugging Face 系统凭据"
            ) from exc

    def delete(self) -> None:
        if self.load() is None:
            return
        try:
            self._backend.delete_password(
                CREDENTIAL_SERVICE,
                CREDENTIAL_ACCOUNT,
            )
        except Exception as exc:
            raise CredentialStoreError(
                "无法删除 Watcher Hugging Face 系统凭据"
            ) from exc
