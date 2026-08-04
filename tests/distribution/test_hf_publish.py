from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from huggingface_hub import CommitOperationAdd, CommitOperationDelete
from huggingface_hub.errors import HfHubHTTPError

from watcherobot.distribution.hf_publish import HuggingFacePublishHubClient
from watcherobot.distribution.ports import (
    AccessToken,
    CatalogDocument,
    HubAuthenticationError,
    HubCatalogConflict,
    HubInvalidResponse,
    HubNetworkError,
    HubRepositoryConflict,
    UploadFile,
)


SPACE_ID = "developer/WatcherRobot-com.orulink.demo"
CATALOG_REPO = "Orulink/watcherobot-app-store"
COMMIT = "a" * 40


@dataclass
class FakeHfApi:
    repo_exists_value: bool = False
    repo_files: list[str] = field(default_factory=list)
    repo_sha: str = COMMIT
    downloaded_path: Path | None = None
    discussions: list[object] = field(default_factory=list)
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    failure: tuple[str, Exception] | None = None

    def _record(self, name: str, kwargs: dict[str, object]) -> None:
        self.calls.append((name, kwargs))
        if self.failure is not None and self.failure[0] == name:
            raise self.failure[1]

    def repo_exists(self, **kwargs):
        self._record("repo_exists", kwargs)
        return self.repo_exists_value

    def create_repo(self, **kwargs):
        self._record("create_repo", kwargs)
        return SimpleNamespace(repo_id=kwargs["repo_id"])

    def list_repo_files(self, **kwargs):
        self._record("list_repo_files", kwargs)
        return list(self.repo_files)

    def create_commit(self, **kwargs):
        self._record("create_commit", kwargs)
        return SimpleNamespace(
            pr_url=(
                "https://huggingface.co/datasets/Orulink/"
                "watcherobot-app-store/discussions/11"
            ),
            pr_num=11,
        )

    def repo_info(self, **kwargs):
        self._record("repo_info", kwargs)
        return SimpleNamespace(sha=self.repo_sha)

    def hf_hub_download(self, **kwargs):
        self._record("hf_hub_download", kwargs)
        assert self.downloaded_path is not None
        return str(self.downloaded_path)

    def get_repo_discussions(self, **kwargs):
        self._record("get_repo_discussions", kwargs)
        return iter(self.discussions)


@dataclass
class FakeApiFactory:
    api: FakeHfApi
    tokens: list[str] = field(default_factory=list)

    def __call__(self, token: str):
        self.tokens.append(token)
        return self.api


def _client(api: FakeHfApi) -> tuple[HuggingFacePublishHubClient, FakeApiFactory]:
    factory = FakeApiFactory(api)
    return HuggingFacePublishHubClient(api_factory=factory), factory


@pytest.mark.parametrize("existed", [False, True])
def test_ensure_space_is_public_static_and_uses_only_explicit_token(
    existed: bool,
) -> None:
    api = FakeHfApi(repo_exists_value=existed)
    client, factory = _client(api)

    result = client.ensure_public_space(
        AccessToken("watcher-oauth-token"),
        space_id=SPACE_ID,
        sdk="static",
    )

    assert result.space_id == SPACE_ID
    assert result.created is (not existed)
    assert factory.tokens == ["watcher-oauth-token"]
    assert api.calls == [
        (
            "repo_exists",
            {"repo_id": SPACE_ID, "repo_type": "space"},
        ),
        (
            "create_repo",
            {
                "repo_id": SPACE_ID,
                "repo_type": "space",
                "private": False,
                "exist_ok": True,
                "space_sdk": "static",
            },
        ),
    ]


def test_replace_space_files_deletes_stale_paths_and_adds_exact_snapshot(
    tmp_path: Path,
) -> None:
    app_path = tmp_path / "app.py"
    app_path.write_text("print('demo')\n", encoding="utf-8")
    api = FakeHfApi(repo_files=["README.md", "stale.txt"])
    client, _factory = _client(api)

    client.replace_space_files(
        AccessToken("watcher-oauth-token"),
        space_id=SPACE_ID,
        files=(
            UploadFile.from_bytes("README.md", b"---\nsdk: static\n---\n"),
            UploadFile.from_path("app.py", app_path),
        ),
        commit_message="Publish demo",
    )

    list_call, commit_call = api.calls
    assert list_call == (
        "list_repo_files",
        {"repo_id": SPACE_ID, "repo_type": "space", "revision": "main"},
    )
    assert commit_call[0] == "create_commit"
    assert commit_call[1]["repo_id"] == SPACE_ID
    assert commit_call[1]["repo_type"] == "space"
    assert commit_call[1]["revision"] == "main"
    assert commit_call[1]["commit_message"] == "Publish demo"
    operations = commit_call[1]["operations"]
    assert isinstance(operations, list)
    deletes = [op for op in operations if isinstance(op, CommitOperationDelete)]
    adds = [op for op in operations if isinstance(op, CommitOperationAdd)]
    assert [op.path_in_repo for op in deletes] == ["stale.txt"]
    assert [op.path_in_repo for op in adds] == ["README.md", "app.py"]
    assert adds[0].path_or_fileobj == b"---\nsdk: static\n---\n"
    assert adds[1].path_or_fileobj == str(app_path)


def test_get_space_head_returns_fixed_tree_url() -> None:
    client, _factory = _client(FakeHfApi(repo_sha=COMMIT))

    revision = client.get_space_head(
        AccessToken("watcher-oauth-token"),
        space_id=SPACE_ID,
    )

    assert revision.commit == COMMIT
    assert revision.url == (
        f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}"
    )


def test_get_space_head_rejects_floating_or_missing_sha() -> None:
    client, _factory = _client(FakeHfApi(repo_sha="main"))

    with pytest.raises(HubInvalidResponse, match="full commit"):
        client.get_space_head(
            AccessToken("watcher-oauth-token"),
            space_id=SPACE_ID,
        )


def test_read_space_file_pins_the_requested_commit(
    tmp_path: Path,
) -> None:
    downloaded = tmp_path / "app.json"
    downloaded.write_bytes(b'{"schema_version":1}')
    api = FakeHfApi(downloaded_path=downloaded)
    client, factory = _client(api)

    content = client.read_space_file(
        AccessToken("watcher-oauth-token"),
        space_id=SPACE_ID,
        commit=COMMIT,
        path="app.json",
    )

    assert content == b'{"schema_version":1}'
    assert factory.tokens == ["watcher-oauth-token"]
    assert api.calls == [
        (
            "hf_hub_download",
            {
                "repo_id": SPACE_ID,
                "repo_type": "space",
                "filename": "app.json",
                "revision": COMMIT,
            },
        )
    ]


def test_read_catalog_pins_download_to_observed_parent_commit(
    tmp_path: Path,
) -> None:
    downloaded = tmp_path / "app-list.json"
    downloaded.write_bytes(b"[]\n")
    api = FakeHfApi(repo_sha=COMMIT, downloaded_path=downloaded)
    client, _factory = _client(api)

    document = client.read_catalog(
        AccessToken("watcher-oauth-token"),
        repo_id=CATALOG_REPO,
        path="app-list.json",
    )

    assert document == CatalogDocument(content=b"[]\n", commit=COMMIT)
    assert api.calls[-1] == (
        "hf_hub_download",
        {
            "repo_id": CATALOG_REPO,
            "repo_type": "dataset",
            "filename": "app-list.json",
            "revision": COMMIT,
        },
    )


def test_list_open_catalog_pull_requests_preserves_public_metadata() -> None:
    api = FakeHfApi(
        discussions=[
            SimpleNamespace(num=3, title="WatcherRobot catalog: app@sha", status="open")
        ]
    )
    client, _factory = _client(api)

    pull_requests = client.list_open_catalog_pull_requests(
        AccessToken("watcher-oauth-token"),
        repo_id=CATALOG_REPO,
        author="developer",
    )

    assert len(pull_requests) == 1
    assert pull_requests[0].number == 3
    assert pull_requests[0].status == "open"
    assert pull_requests[0].url.endswith("/discussions/3")
    assert api.calls[-1] == (
        "get_repo_discussions",
        {
            "repo_id": CATALOG_REPO,
            "repo_type": "dataset",
            "author": "developer",
            "discussion_type": "pull_request",
            "discussion_status": "open",
        },
    )


def test_create_catalog_pr_uses_parent_commit_and_only_catalog_file() -> None:
    api = FakeHfApi()
    client, _factory = _client(api)

    pull_request = client.create_catalog_pull_request(
        AccessToken("watcher-oauth-token"),
        repo_id=CATALOG_REPO,
        path="app-list.json",
        content=b"[]\n",
        parent_commit=COMMIT,
        title="WatcherRobot catalog: demo@commit",
        description="Review this entry.",
    )

    assert pull_request.number == 11
    call = api.calls[-1]
    assert call[0] == "create_commit"
    assert call[1]["repo_id"] == CATALOG_REPO
    assert call[1]["repo_type"] == "dataset"
    assert call[1]["parent_commit"] == COMMIT
    assert call[1]["create_pr"] is True
    operations = call[1]["operations"]
    assert isinstance(operations, list)
    assert len(operations) == 1
    assert isinstance(operations[0], CommitOperationAdd)
    assert operations[0].path_in_repo == "app-list.json"


@pytest.mark.parametrize(
    ("operation", "status", "expected_error"),
    [
        ("create_repo", 401, HubAuthenticationError),
        ("create_repo", 403, HubRepositoryConflict),
        ("create_repo", 409, HubRepositoryConflict),
        ("create_commit", 403, HubRepositoryConflict),
    ],
)
def test_space_http_errors_are_sanitized(
    operation: str,
    status: int,
    expected_error: type[Exception],
) -> None:
    request = httpx.Request("POST", "https://huggingface.co/api/repos/create")
    response = httpx.Response(status, request=request)
    api = FakeHfApi(
        failure=(
            operation,
            HfHubHTTPError("leaked watcher-oauth-token", response=response),
        )
    )
    client, _factory = _client(api)

    with pytest.raises(expected_error) as captured:
        if operation == "create_repo":
            client.ensure_public_space(
                AccessToken("watcher-oauth-token"),
                space_id=SPACE_ID,
                sdk="static",
            )
        else:
            client.replace_space_files(
                AccessToken("watcher-oauth-token"),
                space_id=SPACE_ID,
                files=(UploadFile.from_bytes("README.md", b"readme"),),
                commit_message="Publish",
            )

    assert "watcher-oauth-token" not in str(captured.value)


def test_catalog_parent_conflict_has_specific_sanitized_error() -> None:
    request = httpx.Request("POST", "https://huggingface.co/api/commit")
    response = httpx.Response(409, request=request)
    api = FakeHfApi(
        failure=(
            "create_commit",
            HfHubHTTPError("leaked watcher-oauth-token", response=response),
        )
    )
    client, _factory = _client(api)

    with pytest.raises(HubCatalogConflict) as captured:
        client.create_catalog_pull_request(
            AccessToken("watcher-oauth-token"),
            repo_id=CATALOG_REPO,
            path="app-list.json",
            content=b"[]\n",
            parent_commit=COMMIT,
            title="title",
            description="description",
        )

    assert "watcher-oauth-token" not in str(captured.value)


def test_transport_failure_maps_to_sanitized_network_error() -> None:
    request = httpx.Request("GET", "https://huggingface.co/api/repo")
    api = FakeHfApi(
        failure=(
            "repo_info",
            httpx.ConnectError("leaked watcher-oauth-token", request=request),
        )
    )
    client, _factory = _client(api)

    with pytest.raises(HubNetworkError) as captured:
        client.get_space_head(
            AccessToken("watcher-oauth-token"),
            space_id=SPACE_ID,
        )

    assert "watcher-oauth-token" not in str(captured.value)
