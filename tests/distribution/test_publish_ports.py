from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from watcherobot.distribution.ports import (
    AccessToken,
    CatalogDocument,
    CatalogPullRequest,
    PublishHubClient,
    RepositoryRevision,
    SpaceRepository,
    UploadFile,
)


@dataclass
class FakePublishHubClient:
    calls: list[tuple[str, object]] = field(default_factory=list)

    def ensure_public_space(
        self,
        token: AccessToken,
        *,
        space_id: str,
        sdk: str,
    ) -> SpaceRepository:
        self.calls.append(("ensure_public_space", (token, space_id, sdk)))
        return SpaceRepository(space_id=space_id, created=True)

    def replace_space_files(
        self,
        token: AccessToken,
        *,
        space_id: str,
        files: tuple[UploadFile, ...],
        commit_message: str,
    ) -> None:
        self.calls.append(
            (
                "replace_space_files",
                (token, space_id, files, commit_message),
            )
        )

    def get_space_head(
        self,
        token: AccessToken,
        *,
        space_id: str,
    ) -> RepositoryRevision:
        self.calls.append(("get_space_head", (token, space_id)))
        return RepositoryRevision(
            commit="a" * 40,
            url=f"https://huggingface.co/spaces/{space_id}/tree/{'a' * 40}",
        )

    def read_catalog(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        path: str,
    ) -> CatalogDocument:
        self.calls.append(("read_catalog", (token, repo_id, path)))
        return CatalogDocument(content=b"[]\n", commit="b" * 40)

    def list_open_catalog_pull_requests(
        self,
        token: AccessToken,
        *,
        repo_id: str,
        author: str,
    ) -> tuple[CatalogPullRequest, ...]:
        self.calls.append(
            ("list_open_catalog_pull_requests", (token, repo_id, author))
        )
        return ()

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
                "create_catalog_pull_request",
                (
                    token,
                    repo_id,
                    path,
                    content,
                    parent_commit,
                    title,
                    description,
                ),
            )
        )
        return CatalogPullRequest(
            number=7,
            title=title,
            url=f"https://huggingface.co/datasets/{repo_id}/discussions/7",
            status="open",
        )


def test_publish_port_accepts_injected_fake_for_complete_remote_boundary(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "app.py"
    source_path.write_text("print('demo')\n", encoding="utf-8")
    token = AccessToken("hf_secret-token")
    hub: PublishHubClient = FakePublishHubClient()
    space_id = "developer/WatcherRobot-com.orulink.demo"
    upload_files = (
        UploadFile.from_path("app.py", source_path),
        UploadFile.from_bytes("README.md", b"---\nsdk: static\n---\n"),
    )

    space = hub.ensure_public_space(
        token,
        space_id=space_id,
        sdk="static",
    )
    hub.replace_space_files(
        token,
        space_id=space_id,
        files=upload_files,
        commit_message="Publish com.orulink.demo 1.0.0",
    )
    revision = hub.get_space_head(token, space_id=space_id)
    catalog = hub.read_catalog(
        token,
        repo_id="Orulink/watcherobot-app-store",
        path="app-list.json",
    )
    pull_requests = hub.list_open_catalog_pull_requests(
        token,
        repo_id="Orulink/watcherobot-app-store",
        author="developer",
    )
    pull_request = hub.create_catalog_pull_request(
        token,
        repo_id="Orulink/watcherobot-app-store",
        path="app-list.json",
        content=b'[{"space_id":"developer/WatcherRobot-com.orulink.demo",'
        b'"commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]\n',
        parent_commit=catalog.commit,
        title="Publish developer/WatcherRobot-com.orulink.demo",
        description="Request catalog review.",
    )

    assert space == SpaceRepository(space_id=space_id, created=True)
    assert revision.commit == "a" * 40
    assert revision.url.endswith("/tree/" + "a" * 40)
    assert catalog.content == b"[]\n"
    assert pull_requests == ()
    assert pull_request.number == 7
    assert pull_request.status == "open"


def test_upload_file_hides_local_path_and_generated_content(
    tmp_path: Path,
) -> None:
    local_path = tmp_path / "secret-local-path.py"
    local_path.write_text("TOKEN = 'not-published'\n", encoding="utf-8")
    from_path = UploadFile.from_path("app.py", local_path)
    from_bytes = UploadFile.from_bytes(
        "README.md",
        b"hf_secret-generated-content",
    )

    assert str(local_path) not in repr(from_path)
    assert "hf_secret-generated-content" not in repr(from_bytes)


def test_revision_and_catalog_document_require_full_commit_sha() -> None:
    import pytest

    with pytest.raises(ValueError, match="40-character"):
        RepositoryRevision(commit="main", url="https://example.invalid")
    with pytest.raises(ValueError, match="40-character"):
        CatalogDocument(content=b"[]", commit="abc123")
