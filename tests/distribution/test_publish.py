from __future__ import annotations

from pathlib import Path

import pytest

from watcherobot.distribution.events import ErrorCode
from watcherobot.distribution.ports import AccessToken
from watcherobot.distribution.publish import PublishError, publish_application
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
)

from tests.distribution._publishing_fakes import (
    SPACE_COMMIT,
    SPACE_ID,
    FakeCredentialStore,
    FakeIdentityHub,
    FakePublishHub,
    RecordingEvents,
    write_application,
)


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
def test_publish_only_uploads_source_and_returns_immutable_commit(
    tmp_path: Path,
    space_created: bool,
) -> None:
    write_application(tmp_path)
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
    }
    assert [name for name, _ in hub.calls] == ["ensure", "upload", "head"]
    assert [event.stage for event in events.events] == [
        "checking",
        "authenticating",
        "ensuring_space",
        "uploading_source",
        "resolving_commit",
    ]
    paths = {item.path_in_repo for item in hub.uploaded_files}
    assert paths == {"README.md", "app.json", "app.py", "icon.png"}
    assert ".env" not in paths
    assert "index.html" not in paths


def test_publish_does_not_require_storefront_metadata(tmp_path: Path) -> None:
    write_application(tmp_path, description="", author="", icon="")
    hub = FakePublishHub()

    result = _publish(tmp_path, hub)

    assert result.commit == SPACE_COMMIT
    assert [name for name, _ in hub.calls] == ["ensure", "upload", "head"]
    assert {item.path_in_repo for item in hub.uploaded_files} == {
        "README.md",
        "app.json",
        "app.py",
    }


def test_local_check_happens_before_credentials_or_remote_calls(
    tmp_path: Path,
) -> None:
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
    write_application(tmp_path)
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
    ("failure", "expected_code", "expected_calls"),
    [
        ("ensure", ErrorCode.REMOTE_ERROR, ["ensure"]),
        ("ownership", ErrorCode.SPACE_OWNERSHIP_CONFLICT, ["ensure"]),
        ("upload", ErrorCode.REMOTE_ERROR, ["ensure", "upload"]),
        ("commit", ErrorCode.REMOTE_ERROR, ["ensure", "upload", "head"]),
    ],
)
def test_publish_remote_failures_are_sanitized_and_stop_following_steps(
    tmp_path: Path,
    failure: str,
    expected_code: ErrorCode,
    expected_calls: list[str],
) -> None:
    write_application(tmp_path)
    hub = FakePublishHub(failure=failure)

    with pytest.raises(PublishError) as captured:
        _publish(tmp_path, hub)

    assert captured.value.code is expected_code
    assert [name for name, _ in hub.calls] == expected_calls
