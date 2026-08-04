from __future__ import annotations

import json
from pathlib import Path

import pytest

from watcherobot.distribution.catalog_submission import (
    catalog_pull_request_title,
)
from watcherobot.distribution.events import ErrorCode
from watcherobot.distribution.ports import (
    AccessToken,
    CatalogDocument,
    CatalogPullRequest,
)
from watcherobot.distribution.submit import SubmitError, submit_application
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
)

from tests.distribution._publishing_fakes import (
    CATALOG_COMMIT,
    CATALOG_REPO,
    SPACE_COMMIT,
    SPACE_ID,
    FakeCredentialStore,
    FakeIdentityHub,
    FakePublishHub,
    RecordingEvents,
    manifest_document,
    write_application,
)


def _submit(
    root: Path,
    publish_hub: FakePublishHub,
    *,
    commit: str | None = None,
    credentials: FakeCredentialStore | None = None,
    identity_hub: FakeIdentityHub | None = None,
    events: RecordingEvents | None = None,
):
    return submit_application(
        root,
        commit=commit,
        credentials=credentials or FakeCredentialStore(AccessToken("token")),
        identity_hub=identity_hub or FakeIdentityHub(),
        publish_hub=publish_hub,
        events=events or RecordingEvents(),
        watcherobot_version="0.1.1a1",
    )


def test_submit_uses_published_snapshot_and_never_uploads_source(
    tmp_path: Path,
) -> None:
    write_application(tmp_path)
    hub = FakePublishHub()
    events = RecordingEvents()

    result = _submit(tmp_path, hub, events=events)

    assert result.to_dict() == {
        "space_id": SPACE_ID,
        "commit": SPACE_COMMIT,
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
        "head",
        "read_space_file",
        "read_space_file",
        "read_catalog",
        "list_prs",
        "create_pr",
    ]
    assert [event.stage for event in events.events] == [
        "checking",
        "authenticating",
        "resolving_commit",
        "verifying_source",
        "updating_catalog",
    ]
    create_pr_call = hub.calls[-1][1]
    assert isinstance(create_pr_call, tuple)
    assert create_pr_call[0] == CATALOG_REPO
    assert create_pr_call[3] == CATALOG_COMMIT
    assert create_pr_call[4] == catalog_pull_request_title(
        SPACE_ID,
        SPACE_COMMIT,
    )
    assert json.loads(create_pr_call[2]) == [
        {"space_id": SPACE_ID, "commit": SPACE_COMMIT}
    ]
    description = create_pr_call[5]
    assert isinstance(description, str)
    assert "| Name | Demo |" in description
    assert "| Author | Developer |" in description
    assert f"[View fixed source]({result.source_url})" in description


def test_submit_can_target_an_explicit_published_commit(tmp_path: Path) -> None:
    write_application(tmp_path)
    hub = FakePublishHub()
    fixed_commit = "c" * 40

    result = _submit(tmp_path, hub, commit=fixed_commit)

    assert result.commit == fixed_commit
    assert "head" not in [name for name, _ in hub.calls]
    assert hub.calls[0] == (
        "read_space_file",
        (SPACE_ID, fixed_commit, "app.json"),
    )


@pytest.mark.parametrize("missing_field", ["description", "author", "icon"])
def test_submit_requires_complete_storefront_metadata_before_remote_calls(
    tmp_path: Path,
    missing_field: str,
) -> None:
    write_application(tmp_path, **{missing_field: ""})
    credentials = FakeCredentialStore(AccessToken("token"))
    identity_hub = FakeIdentityHub()
    hub = FakePublishHub()

    with pytest.raises(ApplicationManifestError) as captured:
        _submit(
            tmp_path,
            hub,
            credentials=credentials,
            identity_hub=identity_hub,
        )

    assert str(captured.value) == (
        "Catalog submission requires non-empty app.json fields: "
        f"{missing_field}"
    )
    assert credentials.load_count == 0
    assert identity_hub.calls == []
    assert hub.calls == []


def test_submit_rejects_local_manifest_that_differs_from_fixed_source(
    tmp_path: Path,
) -> None:
    write_application(tmp_path)
    hub = FakePublishHub(remote_manifest=manifest_document(version="2.0.0"))

    with pytest.raises(SubmitError) as captured:
        _submit(tmp_path, hub)

    assert captured.value.code is ErrorCode.APP_MANIFEST_INVALID
    assert "does not match the published commit" in str(captured.value)
    assert [name for name, _ in hub.calls] == ["head", "read_space_file"]


def test_catalog_already_contains_commit_without_creating_pr(
    tmp_path: Path,
) -> None:
    write_application(tmp_path)
    hub = FakePublishHub(
        catalog=CatalogDocument(
            content=json.dumps(
                [{"space_id": SPACE_ID, "commit": SPACE_COMMIT}]
            ).encode("utf-8"),
            commit=CATALOG_COMMIT,
        )
    )

    result = _submit(tmp_path, hub)

    assert result.pr_status == "already_listed"
    assert result.pr_url == ""
    assert "create_pr" not in [name for name, _ in hub.calls]


def test_same_open_pull_request_is_reused(tmp_path: Path) -> None:
    write_application(tmp_path)
    existing = CatalogPullRequest(
        number=9,
        title=catalog_pull_request_title(SPACE_ID, SPACE_COMMIT),
        url="https://huggingface.co/datasets/catalog/discussions/9",
        status="open",
    )
    hub = FakePublishHub(open_pull_requests=(existing,))

    result = _submit(tmp_path, hub)

    assert result.pr_status == "pending"
    assert result.pr_url == existing.url
    assert "create_pr" not in [name for name, _ in hub.calls]


def test_different_open_pull_request_reports_conflict_without_upload(
    tmp_path: Path,
) -> None:
    write_application(tmp_path)
    existing = CatalogPullRequest(
        number=10,
        title=catalog_pull_request_title(SPACE_ID, "c" * 40),
        url="https://huggingface.co/datasets/catalog/discussions/10",
        status="open",
    )
    hub = FakePublishHub(open_pull_requests=(existing,))

    with pytest.raises(SubmitError) as captured:
        _submit(tmp_path, hub)

    assert captured.value.code is ErrorCode.CATALOG_PR_CONFLICT
    assert captured.value.details["pr_url"] == existing.url
    assert "ensure" not in [name for name, _ in hub.calls]
    assert "upload" not in [name for name, _ in hub.calls]


def test_invalid_remote_catalog_has_stable_error(tmp_path: Path) -> None:
    write_application(tmp_path)
    hub = FakePublishHub(
        catalog=CatalogDocument(
            content=b'{"not":"a list"}',
            commit=CATALOG_COMMIT,
        )
    )

    with pytest.raises(SubmitError) as captured:
        _submit(tmp_path, hub)

    assert captured.value.code is ErrorCode.CATALOG_INVALID
