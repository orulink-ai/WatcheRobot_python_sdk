"""ModelScope Dataset adapters for Application source publishing."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from .ports import (
    AccessToken,
    HubAuthenticationError,
    HubIdentity,
    HubInvalidResponse,
    HubNetworkError,
    HubRepositoryConflict,
    RepositoryRevision,
    SourceRepository,
    UploadFile,
)


_FULL_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ApiFactory = Callable[[str], Any]
RefResolver = Callable[[str], str]


class ModelScopeHubClient:
    """Resolve the account owned by one explicitly supplied ModelScope token."""

    def __init__(self, *, api_factory: ApiFactory | None = None) -> None:
        self._api_factory = api_factory or _default_api_factory

    def whoami(self, token: AccessToken) -> HubIdentity:
        try:
            user = self._api_factory(token.value).whoami()
        except Exception as exc:
            _raise_hub_error(exc)
        username = getattr(user, "username", None)
        if not isinstance(username, str) or not username:
            raise HubInvalidResponse("ModelScope returned an invalid account identity")
        display_name = getattr(user, "display_name", "")
        return HubIdentity(
            username=username,
            display_name=display_name if isinstance(display_name, str) else "",
        )


class ModelScopePublishHubClient:
    """Publish one exact Application source tree to a public Dataset repo."""

    provider = "modelscope"
    display_name = "ModelScope"
    repository_type = "dataset"

    def __init__(
        self,
        *,
        api_factory: ApiFactory | None = None,
        ref_resolver: RefResolver | None = None,
    ) -> None:
        self._api_factory = api_factory or _default_api_factory
        self._ref_resolver = ref_resolver or _resolve_public_head

    def repository_url(self, repository_id: str) -> str:
        return f"https://modelscope.cn/datasets/{repository_id}"

    def ensure_public_repository(
        self,
        token: AccessToken,
        *,
        repository_id: str,
    ) -> SourceRepository:
        api = self._api_factory(token.value)
        created = False
        try:
            info = api.get_repo(repository_id, self.repository_type)
        except Exception as exc:
            if type(exc).__name__ != "NotExistError":
                _raise_hub_error(exc, repository_conflict=True)
            try:
                info = api.create_repo(
                    repository_id,
                    self.repository_type,
                    visibility="public",
                    description="WatcheRobot Application source snapshot",
                )
                created = True
            except Exception as create_exc:
                _raise_hub_error(create_exc, repository_conflict=True)

        private = getattr(info, "private", None)
        visibility = getattr(info, "visibility", None)
        visibility_value = str(visibility).lower()
        if private is True or (
            visibility is not None
            and visibility_value not in {"public", "5", "visibility.public"}
        ):
            raise HubRepositoryConflict(
                "The existing ModelScope repository is not public"
            )
        return SourceRepository(
            repository_id=repository_id,
            repository_type=self.repository_type,
            created=created,
        )

    def replace_repository_files(
        self,
        token: AccessToken,
        *,
        repository_id: str,
        files: tuple[UploadFile, ...],
        commit_message: str,
    ) -> None:
        desired = {item.path_in_repo: item for item in files}
        if len(desired) != len(files):
            raise HubInvalidResponse(
                "Application upload contains duplicate repository paths"
            )
        api = self._api_factory(token.value)
        try:
            for item in sorted(files, key=lambda value: value.path_in_repo):
                source: str | Path | bytes
                if item.source_path is not None:
                    if not item.source_path.is_file():
                        raise HubInvalidResponse(
                            "Application source changed before upload completed"
                        )
                    source = item.source_path
                else:
                    assert item.content is not None
                    source = item.content
                api.upload_file(
                    repository_id,
                    self.repository_type,
                    source,
                    item.path_in_repo,
                    revision="master",
                    commit_message=commit_message,
                    disable_tqdm=True,
                )

            remote_files = api.list_repo_files(
                repository_id,
                self.repository_type,
                revision="master",
            )
            remote_paths = {
                entry.path
                for entry in remote_files
                if getattr(entry, "sha256", None) and entry.path != ".gitattributes"
            }
            stale_paths = sorted(remote_paths - set(desired))
            if stale_paths:
                api.delete_files(
                    repository_id,
                    self.repository_type,
                    stale_paths,
                    revision="master",
                    commit_message=commit_message,
                )
            self._verify_uploaded_files(api, repository_id, desired)
        except HubInvalidResponse:
            raise
        except Exception as exc:
            _raise_hub_error(exc, repository_conflict=True)

    def get_repository_head(
        self,
        token: AccessToken,
        *,
        repository_id: str,
    ) -> RepositoryRevision:
        del token  # The publication repository is public by contract.
        try:
            commit = self._ref_resolver(repository_id)
        except Exception as exc:
            raise HubNetworkError(
                "Unable to resolve the ModelScope repository commit"
            ) from exc
        if _FULL_COMMIT_PATTERN.fullmatch(commit) is None:
            raise HubInvalidResponse(
                "ModelScope returned an invalid repository commit"
            )
        return RepositoryRevision(
            commit=commit,
            url=f"{self.repository_url(repository_id)}/files?version={commit}",
        )

    def _verify_uploaded_files(
        self,
        api: Any,
        repository_id: str,
        desired: dict[str, UploadFile],
    ) -> None:
        remote_files = api.list_repo_files(
            repository_id,
            self.repository_type,
            revision="master",
        )
        remote_hashes = {
            entry.path: getattr(entry, "sha256", None)
            for entry in remote_files
            if getattr(entry, "sha256", None)
        }
        if set(desired) - set(remote_hashes):
            raise HubInvalidResponse("ModelScope upload verification missed files")
        for path, item in desired.items():
            content = (
                item.source_path.read_bytes()
                if item.source_path is not None
                else item.content
            )
            assert content is not None
            if hashlib.sha256(content).hexdigest() != remote_hashes[path]:
                raise HubInvalidResponse("ModelScope upload verification failed")


def _default_api_factory(token: str) -> Any:
    try:
        from modelscope_hub import HubApi
    except ImportError as exc:
        raise HubInvalidResponse(
            "ModelScope publishing requires the modelscope-hub package"
        ) from exc
    return HubApi(endpoint="https://modelscope.cn", token=token)


def _resolve_public_head(repository_id: str) -> str:
    url = f"https://modelscope.cn/datasets/{repository_id}.git"
    completed = subprocess.run(
        ["git", "-c", "credential.helper=", "ls-remote", url, "refs/heads/master"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    first_line = completed.stdout.splitlines()[0]
    return first_line.split(maxsplit=1)[0]


def _raise_hub_error(
    exc: Exception,
    *,
    repository_conflict: bool = False,
) -> NoReturn:
    name = type(exc).__name__.lower()
    if "auth" in name or "permission" in name or "forbidden" in name:
        raise HubAuthenticationError("ModelScope authentication failed") from exc
    if repository_conflict and any(
        marker in name for marker in ("conflict", "invalid", "badrequest")
    ):
        raise HubRepositoryConflict("ModelScope repository conflict") from exc
    if any(marker in name for marker in ("timeout", "connection", "network")):
        raise HubNetworkError("Unable to reach ModelScope") from exc
    raise HubNetworkError("ModelScope request failed") from exc
