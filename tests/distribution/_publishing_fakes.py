from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from watcherobot.distribution.events import ProgressEvent
from watcherobot.distribution.ports import (
    AccessToken,
    CatalogDocument,
    CatalogPullRequest,
    HubCatalogConflict,
    HubIdentity,
    HubInvalidResponse,
    HubNetworkError,
    HubRepositoryConflict,
    RepositoryRevision,
    SpaceRepository,
    UploadFile,
)


SPACE_ID = "developer/WatcherRobot-com.orulink.demo"
SPACE_COMMIT = "a" * 40
CATALOG_COMMIT = "b" * 40
CATALOG_REPO = "Orulink/watcherobot-app-store"


def manifest_document(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "schema_version": 1,
        "id": "com.orulink.demo",
        "name": "Demo",
        "version": "1.2.3",
        "requires_watcherobot": ">=0.1,<0.2",
        "dependencies": [],
        "description": "A demo",
        "author": "Developer",
        "icon": "icon.png",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def write_application(root: Path, **manifest_overrides: object) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_bytes(
        manifest_document(**manifest_overrides)
    )
    root.joinpath("app.py").write_text("print('demo')\n", encoding="utf-8")
    icon = manifest_overrides.get("icon", "icon.png")
    if isinstance(icon, str) and icon:
        root.joinpath(icon).write_bytes(b"application-icon")
    root.joinpath(".env").write_text("TOKEN=secret\n", encoding="utf-8")


class FakeCredentialStore:
    def __init__(self, token: AccessToken | None = None) -> None:
        self.token = token
        self.load_count = 0

    def load(self) -> AccessToken | None:
        self.load_count += 1
        return self.token

    def save(self, token: AccessToken) -> None:
        self.token = token

    def delete(self) -> None:
        self.token = None


@dataclass
class FakeIdentityHub:
    identity: HubIdentity = HubIdentity(username="developer")
    calls: list[AccessToken] = field(default_factory=list)

    def whoami(self, token: AccessToken) -> HubIdentity:
        self.calls.append(token)
        return self.identity


@dataclass
class FakePublishHub:
    space_created: bool = True
    catalog: CatalogDocument = CatalogDocument(
        content=b"[]\n",
        commit=CATALOG_COMMIT,
    )
    open_pull_requests: tuple[CatalogPullRequest, ...] = ()
    remote_manifest: bytes = field(default_factory=manifest_document)
    failure: str = ""
    calls: list[tuple[str, object]] = field(default_factory=list)
    uploaded_files: tuple[UploadFile, ...] = ()

    def ensure_public_space(
        self,
        token: AccessToken,
        *,
        space_id: str,
        sdk: str,
    ) -> SpaceRepository:
        self.calls.append(("ensure", (space_id, sdk)))
        if self.failure == "ensure":
            raise HubNetworkError("unavailable")
        if self.failure == "ownership":
            raise HubRepositoryConflict("not owned by this OAuth app")
        return SpaceRepository(space_id=space_id, created=self.space_created)

    def replace_space_files(
        self,
        token: AccessToken,
        *,
        space_id: str,
        files: tuple[UploadFile, ...],
        commit_message: str,
    ) -> None:
        self.calls.append(("upload", (space_id, commit_message)))
        self.uploaded_files = files
        if self.failure == "upload":
            raise HubNetworkError("upload failed")

    def get_space_head(
        self,
        token: AccessToken,
        *,
        space_id: str,
    ) -> RepositoryRevision:
        self.calls.append(("head", space_id))
        if self.failure == "commit":
            raise HubInvalidResponse("missing commit")
        return RepositoryRevision(
            commit=SPACE_COMMIT,
            url=(
                f"https://huggingface.co/spaces/{space_id}/tree/"
                f"{SPACE_COMMIT}"
            ),
        )

    def read_space_file(
        self,
        token: AccessToken,
        *,
        space_id: str,
        commit: str,
        path: str,
    ) -> bytes:
        self.calls.append(("read_space_file", (space_id, commit, path)))
        if self.failure == "source":
            raise HubNetworkError("source unavailable")
        if path == "app.json":
            return self.remote_manifest
        if path == "icon.png":
            return b"application-icon"
        raise HubNetworkError("file unavailable")

    def read_catalog(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        path: str,
    ) -> CatalogDocument:
        self.calls.append(("read_catalog", (repo_id, path)))
        if self.failure == "catalog":
            raise HubNetworkError("catalog unavailable")
        return self.catalog

    def list_open_catalog_pull_requests(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        author: str,
    ) -> tuple[CatalogPullRequest, ...]:
        self.calls.append(("list_prs", (repo_id, author)))
        return self.open_pull_requests

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
        self.calls.append(
            (
                "create_pr",
                (repo_id, path, content, parent_commit, title, description),
            )
        )
        if self.failure == "pr":
            raise HubNetworkError("pull request failed")
        if self.failure == "pr_conflict":
            raise HubCatalogConflict("catalog head changed")
        return CatalogPullRequest(
            number=7,
            title=title,
            url=(
                "https://huggingface.co/datasets/Orulink/"
                "watcherobot-app-store/discussions/7"
            ),
            status="open",
        )


@dataclass
class RecordingEvents:
    events: list[ProgressEvent] = field(default_factory=list)

    def emit(self, event: object) -> None:
        assert isinstance(event, ProgressEvent)
        self.events.append(event)
