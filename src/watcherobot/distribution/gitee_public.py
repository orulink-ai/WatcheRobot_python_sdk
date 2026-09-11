"""Anonymous, immutable Gitee catalog and source-file reads."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from urllib.parse import quote

from .hub_http import JsonTransport, UrllibJsonTransport
from .ports import (
    CatalogDocument, HubFileNotFound, HubInvalidResponse, HubNetworkError,
)

CATALOG_REPO_ID = "orulink-sz/watcherobot-app-store"
CATALOG_PATH = "app-list.json"
_BASE = "https://gitee.com/api/v5/repos/"
_MAX_FILE_BYTES = 1024 * 1024


class GiteePublicRepository:
    """Read metadata without credentials, redirects to download URLs, or archives."""

    def __init__(self, *, transport: JsonTransport | None = None) -> None:
        self._transport = transport or UrllibJsonTransport()

    def _get(self, path: str) -> dict[str, object]:
        try:
            response = self._transport.get_json(
                _BASE + path, {"Accept": "application/json"}, timeout=15.0,
            )
        except HubNetworkError:
            raise HubNetworkError("Gitee public read failed") from None
        except HubInvalidResponse:
            raise HubInvalidResponse("Gitee returned invalid JSON") from None
        if response.status == 404:
            raise HubFileNotFound("Gitee public resource was not found")
        if response.status != 200:
            raise HubNetworkError("Gitee public resource is unavailable")
        if not isinstance(response.payload, dict):
            raise HubInvalidResponse("Gitee returned a non-object response")
        return response.payload

    def read_public_catalog(
        self, *, repo_id: str = CATALOG_REPO_ID, path: str = CATALOG_PATH,
    ) -> CatalogDocument:
        _validate_reference(repo_id, "0" * 40, path)
        branch = self._get(f"{repo_id}/branches/master")
        revision = branch.get("commit")
        commit = revision.get("sha") if isinstance(revision, dict) else None
        _validate_reference(repo_id, commit, path)
        assert isinstance(commit, str)
        return CatalogDocument(
            content=self._read_verified_file(repo_id, commit, path), commit=commit,
        )

    def read_file(self, *, repo_id: str, commit: str, path: str) -> bytes:
        _validate_reference(repo_id, commit, path)
        revision = self._get(f"{repo_id}/commits/{commit}")
        if revision.get("sha") != commit:
            raise HubInvalidResponse("Gitee did not resolve the exact requested commit")
        return self._read_verified_file(repo_id, commit, path)

    def _read_verified_file(self, repo_id: str, commit: str, path: str) -> bytes:
        payload = self._get(f"{repo_id}/contents/{quote(path, safe='/')}?ref={commit}")
        encoded = payload.get("content")
        size = payload.get("size")
        if (
            payload.get("type") != "file"
            or payload.get("encoding") != "base64"
            or not isinstance(encoded, str)
            or type(size) is not int
            or not 0 <= size <= _MAX_FILE_BYTES
            or len(encoded) > 2 * _MAX_FILE_BYTES
        ):
            raise HubInvalidResponse("Gitee returned invalid file metadata")
        try:
            content = base64.b64decode("".join(encoded.split()), validate=True)
        except (ValueError, binascii.Error):
            raise HubInvalidResponse("Gitee returned invalid base64 content") from None
        digest = hashlib.sha1(
            f"blob {len(content)}\0".encode() + content, usedforsecurity=False,
        ).hexdigest()
        if len(content) != size or digest != payload.get("sha"):
            raise HubInvalidResponse("Gitee file content failed Git blob verification")
        return content


def _validate_reference(repo_id: object, commit: object, path: object) -> None:
    if (
        not isinstance(repo_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo_id) is None
        or not isinstance(commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        or not isinstance(path, str)
        or re.fullmatch(r"[A-Za-z0-9_. /-]+", path) is None
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise HubInvalidResponse("Invalid immutable Gitee file reference")
