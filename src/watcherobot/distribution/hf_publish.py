"""Authenticated Hugging Face repository adapter for Application publishing."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import httpx
from huggingface_hub import (
    CommitOperationAdd,
    CommitOperationDelete,
    HfApi,
)
from huggingface_hub.errors import HfHubHTTPError

from watcherobot import __version__

from .ports import (
    AccessToken,
    CatalogDocument,
    CatalogPullRequest,
    HubAuthenticationError,
    HubCatalogConflict,
    HubInvalidResponse,
    HubNetworkError,
    HubRepositoryConflict,
    RepositoryRevision,
    SpaceRepository,
    UploadFile,
)


_FULL_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ApiFactory = Callable[[str], Any]


class HuggingFacePublishHubClient:
    """Implement PublishHubClient through an explicitly-tokenized HfApi."""

    def __init__(self, *, api_factory: ApiFactory | None = None) -> None:
        self._api_factory = api_factory or _default_api_factory

    def ensure_public_space(
        self,
        token: AccessToken,
        *,
        space_id: str,
        sdk: str,
    ) -> SpaceRepository:
        api = self._api_factory(token.value)
        try:
            existed = bool(api.repo_exists(repo_id=space_id, repo_type="space"))
            api.create_repo(
                repo_id=space_id,
                repo_type="space",
                private=False,
                exist_ok=True,
                space_sdk=sdk,
            )
        except Exception as exc:
            _raise_hub_error(exc, repository_conflict=True)
        return SpaceRepository(space_id=space_id, created=not existed)

    def replace_space_files(
        self,
        token: AccessToken,
        *,
        space_id: str,
        files: tuple[UploadFile, ...],
        commit_message: str,
    ) -> None:
        desired_paths = [item.path_in_repo for item in files]
        if len(set(desired_paths)) != len(desired_paths):
            raise HubInvalidResponse(
                "Application upload contains duplicate repository paths"
            )

        api = self._api_factory(token.value)
        try:
            remote_paths = api.list_repo_files(
                repo_id=space_id,
                repo_type="space",
                revision="main",
            )
            operations: list[CommitOperationAdd | CommitOperationDelete] = [
                CommitOperationDelete(path_in_repo=path)
                for path in sorted(set(remote_paths) - set(desired_paths))
            ]
            for upload in sorted(files, key=lambda item: item.path_in_repo):
                source: Path | bytes
                if upload.source_path is not None:
                    if not upload.source_path.is_file():
                        raise HubInvalidResponse(
                            "Application source changed before upload completed"
                        )
                    source = upload.source_path
                else:
                    assert upload.content is not None
                    source = upload.content
                operations.append(
                    CommitOperationAdd(
                        path_in_repo=upload.path_in_repo,
                        path_or_fileobj=source,
                    )
                )
            api.create_commit(
                repo_id=space_id,
                repo_type="space",
                revision="main",
                operations=operations,
                commit_message=commit_message,
            )
        except HubInvalidResponse:
            raise
        except Exception as exc:
            _raise_hub_error(exc, repository_conflict=True)

    def get_space_head(
        self,
        token: AccessToken,
        *,
        space_id: str,
    ) -> RepositoryRevision:
        api = self._api_factory(token.value)
        try:
            info = api.repo_info(
                repo_id=space_id,
                repo_type="space",
                revision="main",
            )
        except Exception as exc:
            _raise_hub_error(exc)
        commit = _full_commit(getattr(info, "sha", None))
        return RepositoryRevision(
            commit=commit,
            url=f"https://huggingface.co/spaces/{space_id}/tree/{commit}",
        )

    def read_catalog(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        path: str,
    ) -> CatalogDocument:
        api = self._api_factory(token.value)
        try:
            info = api.repo_info(
                repo_id=repo_id,
                repo_type="dataset",
                revision="main",
            )
            commit = _full_commit(getattr(info, "sha", None))
            downloaded = api.hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=path,
                revision=commit,
            )
            if not isinstance(downloaded, str):
                raise HubInvalidResponse(
                    "Hugging Face catalog download returned an invalid path"
                )
            content = Path(downloaded).read_bytes()
        except HubInvalidResponse:
            raise
        except Exception as exc:
            _raise_hub_error(exc)
        return CatalogDocument(content=content, commit=commit)

    def list_open_catalog_pull_requests(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        author: str,
    ) -> tuple[CatalogPullRequest, ...]:
        api = self._api_factory(token.value)
        try:
            discussions = api.get_repo_discussions(
                repo_id=repo_id,
                repo_type="dataset",
                author=author,
                discussion_type="pull_request",
                discussion_status="open",
            )
            pull_requests = tuple(
                _to_catalog_pull_request(repo_id, discussion)
                for discussion in discussions
            )
        except HubInvalidResponse:
            raise
        except Exception as exc:
            _raise_hub_error(exc)
        return pull_requests

    def create_catalog_pull_request(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        path: str,
        content: bytes,
        parent_commit: str,
        title: str,
        description: str,
    ) -> CatalogPullRequest:
        api = self._api_factory(token.value)
        try:
            info = api.create_commit(
                repo_id=repo_id,
                repo_type="dataset",
                operations=[
                    CommitOperationAdd(
                        path_in_repo=path,
                        path_or_fileobj=content,
                    )
                ],
                parent_commit=parent_commit,
                commit_message=title,
                commit_description=description,
                create_pr=True,
            )
        except Exception as exc:
            _raise_hub_error(exc, catalog_conflict=True)

        pr_url = getattr(info, "pr_url", None)
        pr_number = getattr(info, "pr_num", None)
        if not isinstance(pr_url, str) or not pr_url:
            raise HubInvalidResponse(
                "Hugging Face did not return the catalog pull request URL"
            )
        if not isinstance(pr_number, int) or pr_number <= 0:
            raise HubInvalidResponse(
                "Hugging Face did not return the catalog pull request number"
            )
        return CatalogPullRequest(
            number=pr_number,
            title=title,
            url=pr_url,
            status="open",
        )


def _default_api_factory(token: str) -> HfApi:
    return HfApi(
        token=token,
        library_name="watcherobot",
        library_version=__version__,
    )


def _full_commit(value: object) -> str:
    if not isinstance(value, str) or _FULL_COMMIT_PATTERN.fullmatch(value) is None:
        raise HubInvalidResponse(
            "Hugging Face repository response is missing a full commit SHA"
        )
    return value


def _to_catalog_pull_request(
    repo_id: str,
    discussion: object,
) -> CatalogPullRequest:
    number = getattr(discussion, "num", None)
    title = getattr(discussion, "title", None)
    status = getattr(discussion, "status", None)
    if (
        not isinstance(number, int)
        or number <= 0
        or not isinstance(title, str)
        or not title
        or not isinstance(status, str)
        or not status
    ):
        raise HubInvalidResponse(
            "Hugging Face returned invalid catalog pull request metadata"
        )
    return CatalogPullRequest(
        number=number,
        title=title,
        url=f"https://huggingface.co/datasets/{repo_id}/discussions/{number}",
        status=status,
    )


def _raise_hub_error(
    error: Exception,
    *,
    repository_conflict: bool = False,
    catalog_conflict: bool = False,
) -> NoReturn:
    status = _http_status(error)
    if status == 401:
        raise HubAuthenticationError(
            "Hugging Face OAuth credential is invalid or expired"
        ) from error
    if repository_conflict and status in {403, 409}:
        raise HubRepositoryConflict(
            "Hugging Face Space cannot be managed by this OAuth App"
        ) from error
    if catalog_conflict and status in {409, 412}:
        raise HubCatalogConflict(
            "Hugging Face catalog changed before pull request creation"
        ) from error
    if status == 403:
        raise HubAuthenticationError(
            "Hugging Face OAuth credential lacks the required permission"
        ) from error
    if isinstance(error, (HfHubHTTPError, httpx.HTTPError, OSError)):
        raise HubNetworkError("Hugging Face repository request failed") from error
    if isinstance(error, ValueError):
        raise HubInvalidResponse(
            "Hugging Face repository request returned invalid data"
        ) from error
    raise HubNetworkError("Hugging Face repository request failed") from error


def _http_status(error: Exception) -> int | None:
    if not isinstance(error, HfHubHTTPError):
        return None
    response = error.response
    return response.status_code if response is not None else None
