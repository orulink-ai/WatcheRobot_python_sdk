from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from watcherobot.distribution.catalog_submission import (
    catalog_pull_request_title,
)
from watcherobot.distribution.events import ErrorCode, ProgressEvent
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
from watcherobot.distribution.publish import (
    PublishError,
    publish_application,
)
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
)


SPACE_ID = "developer/WatcherRobot-com.orulink.demo"
SPACE_COMMIT = "a" * 40
CATALOG_COMMIT = "b" * 40
CATALOG_REPO = "Orulink/watcherobot-app-store"


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

    def read_catalog(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        path: str,
    ) -> CatalogDocument:
        self.calls.append(("read_catalog", (repo_id, path)))
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

    def emit(self, event) -> None:
        assert isinstance(event, ProgressEvent)
        self.events.append(event)


def _write_application(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "com.orulink.demo",
                "name": "Demo",
                "version": "1.2.3",
                "requires_watcherobot": ">=0.1,<0.2",
                "dependencies": [],
                "description": "A demo",
                "author": "Developer",
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text("print('demo')\n", encoding="utf-8")
    root.joinpath(".env").write_text("TOKEN=secret\n", encoding="utf-8")


def _publish(
    root: Path,
    publish_hub: FakePublishHub,
    *,
    credentials: FakeCredentialStore | None = None,
    identity_hub: FakeIdentityHub | None = None,
    events: RecordingEvents | None = None,
):
    return publish_application(
        root,
        credentials=credentials or FakeCredentialStore(AccessToken("token")),
        identity_hub=identity_hub or FakeIdentityHub(),
        publish_hub=publish_hub,
        events=events or RecordingEvents(),
        watcherobot_version="0.1.1a1",
    )


@pytest.mark.parametrize("space_created", [True, False])
def test_publish_orchestrates_create_or_update_and_catalog_pr(
    tmp_path: Path,
    space_created: bool,
) -> None:
    _write_application(tmp_path)
    hub = FakePublishHub(space_created=space_created)
    events = RecordingEvents()

    result = _publish(tmp_path, hub, events=events)

    assert result.to_dict() == {
        "space_id": SPACE_ID,
        "commit": SPACE_COMMIT,
        "space_url": f"https://huggingface.co/spaces/{SPACE_ID}",
        "source_url": (
            f"https://huggingface.co/spaces/{SPACE_ID}/tree/{SPACE_COMMIT}"
        ),
        "pr_url": (
            "https://huggingface.co/datasets/Orulink/"
            "watcherobot-app-store/discussions/7"
        ),
        "pr_status": "pending",
    }
    assert [name for name, _ in hub.calls] == [
        "ensure",
        "upload",
        "head",
        "read_catalog",
        "list_prs",
        "create_pr",
    ]
    assert [event.stage for event in events.events] == [
        "checking",
        "authenticating",
        "ensuring_space",
        "uploading_source",
        "resolving_commit",
        "updating_catalog",
    ]
    paths = {item.path_in_repo for item in hub.uploaded_files}
    assert paths == {"README.md", "app.json", "app.py"}
    assert ".env" not in paths
    assert "index.html" not in paths
    create_pr_call = hub.calls[-1][1]
    assert isinstance(create_pr_call, tuple)
    assert create_pr_call[3] == CATALOG_COMMIT
    assert create_pr_call[4] == catalog_pull_request_title(
        SPACE_ID,
        SPACE_COMMIT,
    )
    assert json.loads(create_pr_call[2]) == [
        {"space_id": SPACE_ID, "commit": SPACE_COMMIT}
    ]


def test_local_check_happens_before_credentials_or_remote_calls(
    tmp_path: Path,
) -> None:
    tmp_path.joinpath("app.json").write_text("{}", encoding="utf-8")
    credentials = FakeCredentialStore(AccessToken("token"))
    identity_hub = FakeIdentityHub()
    publish_hub = FakePublishHub()

    with pytest.raises(ApplicationManifestError):
        _publish(
            tmp_path,
            publish_hub,
            credentials=credentials,
            identity_hub=identity_hub,
        )

    assert credentials.load_count == 0
    assert identity_hub.calls == []
    assert publish_hub.calls == []


def test_missing_watcher_credential_requires_login(tmp_path: Path) -> None:
    _write_application(tmp_path)
    hub = FakePublishHub()

    with pytest.raises(PublishError) as captured:
        _publish(
            tmp_path,
            hub,
            credentials=FakeCredentialStore(),
        )

    assert captured.value.code is ErrorCode.AUTH_REQUIRED
    assert hub.calls == []


@pytest.mark.parametrize(
    ("failure", "expected_code", "last_call"),
    [
        ("ensure", ErrorCode.REMOTE_ERROR, "ensure"),
        ("ownership", ErrorCode.SPACE_OWNERSHIP_CONFLICT, "ensure"),
        ("upload", ErrorCode.REMOTE_ERROR, "upload"),
        ("commit", ErrorCode.REMOTE_ERROR, "head"),
        ("pr", ErrorCode.REMOTE_ERROR, "create_pr"),
        ("pr_conflict", ErrorCode.CATALOG_PR_CONFLICT, "create_pr"),
    ],
)
def test_remote_stage_failures_are_sanitized_and_stop_following_steps(
    tmp_path: Path,
    failure: str,
    expected_code: ErrorCode,
    last_call: str,
) -> None:
    _write_application(tmp_path)
    hub = FakePublishHub(failure=failure)

    with pytest.raises(PublishError) as captured:
        _publish(tmp_path, hub)

    assert captured.value.code is expected_code
    assert [name for name, _ in hub.calls][-1] == last_call
    assert "token" not in str(captured.value)


def test_catalog_already_contains_commit_without_creating_pr(
    tmp_path: Path,
) -> None:
    _write_application(tmp_path)
    hub = FakePublishHub(
        catalog=CatalogDocument(
            content=json.dumps(
                [{"space_id": SPACE_ID, "commit": SPACE_COMMIT}]
            ).encode("utf-8"),
            commit=CATALOG_COMMIT,
        )
    )

    result = _publish(tmp_path, hub)

    assert result.pr_status == "already_listed"
    assert result.pr_url == ""
    assert "create_pr" not in [name for name, _ in hub.calls]


def test_same_open_pull_request_is_returned_without_duplicate(
    tmp_path: Path,
) -> None:
    _write_application(tmp_path)
    existing = CatalogPullRequest(
        number=9,
        title=catalog_pull_request_title(SPACE_ID, SPACE_COMMIT),
        url="https://huggingface.co/datasets/catalog/discussions/9",
        status="open",
    )
    hub = FakePublishHub(open_pull_requests=(existing,))

    result = _publish(tmp_path, hub)

    assert result.pr_status == "pending"
    assert result.pr_url == existing.url
    assert "create_pr" not in [name for name, _ in hub.calls]


def test_different_open_pull_request_reports_conflict_after_source_upload(
    tmp_path: Path,
) -> None:
    _write_application(tmp_path)
    existing = CatalogPullRequest(
        number=10,
        title=catalog_pull_request_title(SPACE_ID, "c" * 40),
        url="https://huggingface.co/datasets/catalog/discussions/10",
        status="open",
    )
    hub = FakePublishHub(open_pull_requests=(existing,))

    with pytest.raises(PublishError) as captured:
        _publish(tmp_path, hub)

    assert captured.value.code is ErrorCode.CATALOG_PR_CONFLICT
    assert captured.value.details == {
        "space_id": SPACE_ID,
        "commit": SPACE_COMMIT,
        "source_url": (
            f"https://huggingface.co/spaces/{SPACE_ID}/tree/{SPACE_COMMIT}"
        ),
        "pr_url": existing.url,
    }
    assert "create_pr" not in [name for name, _ in hub.calls]


def test_invalid_remote_catalog_has_stable_error(tmp_path: Path) -> None:
    _write_application(tmp_path)
    hub = FakePublishHub(
        catalog=CatalogDocument(
            content=b'{"not":"a list"}',
            commit=CATALOG_COMMIT,
        )
    )

    with pytest.raises(PublishError) as captured:
        _publish(tmp_path, hub)

    assert captured.value.code is ErrorCode.CATALOG_INVALID
