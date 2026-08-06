"""Unauthenticated Hugging Face adapter for official marketplace reads."""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

import httpx
from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError

from watcherobot import __version__

from .ports import (
    CatalogDocument,
    HubFileNotFound,
    HubInvalidResponse,
    HubNetworkError,
    HubRepositoryNotFound,
    RepositoryRevision,
    HubRevisionNotFound,
)


_FULL_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_METADATA_REMOVE_ATTEMPTS = 5
_METADATA_REMOVE_DELAY_SECONDS = 0.1
PublicApiFactory = Callable[[], Any]


class HuggingFaceMarketplaceHubClient:
    """Read public catalog and Space files without any user credential."""

    def __init__(self, *, api_factory: PublicApiFactory | None = None) -> None:
        self._api_factory = api_factory or _default_api_factory

    def read_public_catalog(
        self,
        *,
        repo_id: str,
        path: str,
    ) -> CatalogDocument:
        _validate_repo_path(path)
        api = self._api_factory()
        try:
            info = api.repo_info(
                repo_id=repo_id,
                repo_type="dataset",
                revision="main",
            )
        except Exception as exc:
            _raise_public_error(exc, not_found="repository")
        commit = _full_commit(getattr(info, "sha", None))
        try:
            downloaded = api.hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=path,
                revision=commit,
            )
            content = _read_downloaded_file(downloaded)
        except HubInvalidResponse:
            raise
        except Exception as exc:
            _raise_public_error(exc, not_found="file")
        return CatalogDocument(content=content, commit=commit)

    def read_space_file(
        self,
        *,
        space_id: str,
        commit: str,
        path: str,
    ) -> bytes:
        expected_commit = _full_commit(commit)
        _validate_repo_path(path)
        api = self._api_factory()
        _require_exact_space_commit(
            api,
            space_id=space_id,
            commit=expected_commit,
        )
        try:
            downloaded = api.hf_hub_download(
                repo_id=space_id,
                repo_type="space",
                filename=path,
                revision=expected_commit,
            )
            return _read_downloaded_file(downloaded)
        except HubInvalidResponse:
            raise
        except Exception as exc:
            _raise_public_error(exc, not_found="file")

    def download_space_snapshot(
        self,
        *,
        space_id: str,
        commit: str,
        target: Path,
    ) -> RepositoryRevision:
        expected_commit = _full_commit(commit)
        destination = Path(target)
        if destination.is_symlink() or not destination.is_dir():
            raise HubInvalidResponse(
                "Hugging Face snapshot target must be an existing directory"
            )
        try:
            if any(destination.iterdir()):
                raise HubInvalidResponse(
                    "Hugging Face snapshot target must be empty"
                )
        except OSError as exc:
            raise HubInvalidResponse(
                "Hugging Face snapshot target cannot be inspected"
            ) from exc
        destination = destination.resolve()

        api = self._api_factory()
        _require_exact_space_commit(
            api,
            space_id=space_id,
            commit=expected_commit,
        )
        try:
            downloaded = api.snapshot_download(
                repo_id=space_id,
                repo_type="space",
                revision=expected_commit,
                local_dir=destination,
            )
        except Exception as exc:
            _raise_public_error(exc, not_found="revision")
        if not isinstance(downloaded, str):
            raise HubInvalidResponse(
                "Hugging Face snapshot download returned an invalid path"
            )
        if Path(downloaded).resolve() != destination:
            raise HubInvalidResponse(
                "Hugging Face snapshot download did not use the target directory"
            )
        _remove_hub_local_metadata(destination)
        return RepositoryRevision(
            commit=expected_commit,
            url=(
                f"https://huggingface.co/spaces/{space_id}/tree/"
                f"{expected_commit}"
            ),
        )


def _default_api_factory() -> HfApi:
    return HfApi(
        token=False,
        library_name="watcherobot",
        library_version=__version__,
    )


def _full_commit(value: object) -> str:
    if not isinstance(value, str) or _FULL_COMMIT_PATTERN.fullmatch(value) is None:
        raise HubInvalidResponse(
            "Hugging Face repository response requires a full commit SHA"
        )
    return value


def _validate_repo_path(path: str) -> None:
    normalized = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or normalized.is_absolute()
        or ".." in normalized.parts
        or normalized.as_posix() != path
    ):
        raise HubInvalidResponse(
            "Hugging Face repository file path is invalid"
        )


def _read_downloaded_file(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise HubInvalidResponse(
            "Hugging Face download returned an invalid local path"
        )
    try:
        return Path(value).read_bytes()
    except OSError as exc:
        raise HubInvalidResponse(
            "Hugging Face downloaded file cannot be read"
        ) from exc


def _require_exact_space_commit(
    api: Any,
    *,
    space_id: str,
    commit: str,
) -> None:
    try:
        exists = api.repo_exists(repo_id=space_id, repo_type="space")
    except Exception as exc:
        _raise_public_error(exc, not_found="repository")
    if not exists:
        raise HubRepositoryNotFound("Hugging Face Space does not exist")
    try:
        info = api.repo_info(
            repo_id=space_id,
            repo_type="space",
            revision=commit,
        )
    except Exception as exc:
        _raise_public_error(exc, not_found="revision")
    resolved_commit = _full_commit(getattr(info, "sha", None))
    if resolved_commit != commit:
        raise HubInvalidResponse(
            "Hugging Face Space did not resolve the requested commit"
        )


def _remove_hub_local_metadata(destination: Path) -> None:
    metadata = destination / ".cache" / "huggingface"
    if not metadata.exists():
        return
    if metadata.is_symlink() or not metadata.is_dir():
        raise HubInvalidResponse(
            "Hugging Face local download metadata path is unsafe"
        )
    for attempt in range(_METADATA_REMOVE_ATTEMPTS):
        try:
            shutil.rmtree(metadata)
            break
        except OSError:
            if not metadata.exists():
                break
            if attempt == _METADATA_REMOVE_ATTEMPTS - 1:
                break
            time.sleep(_METADATA_REMOVE_DELAY_SECONDS * (attempt + 1))
    try:
        metadata.parent.rmdir()
    except OSError:
        pass


def _raise_public_error(
    error: Exception,
    *,
    not_found: str,
) -> NoReturn:
    status = _http_status(error)
    if status == 404:
        if not_found == "repository":
            raise HubRepositoryNotFound(
                "Hugging Face public repository does not exist"
            ) from error
        if not_found == "revision":
            raise HubRevisionNotFound(
                "Hugging Face fixed commit does not exist"
            ) from error
        raise HubFileNotFound(
            "Hugging Face required repository file does not exist"
        ) from error
    if isinstance(error, (HfHubHTTPError, httpx.HTTPError, OSError)):
        raise HubNetworkError(
            "Hugging Face public repository request failed"
        ) from error
    if isinstance(error, ValueError):
        raise HubInvalidResponse(
            "Hugging Face public repository returned invalid data"
        ) from error
    raise HubNetworkError(
        "Hugging Face public repository request failed"
    ) from error


def _http_status(error: Exception) -> int | None:
    if not isinstance(error, HfHubHTTPError):
        return None
    response = error.response
    return response.status_code if response is not None else None
