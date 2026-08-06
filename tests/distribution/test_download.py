from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from watcherobot.distribution.download import (
    DownloadError,
    download_application_snapshot,
)
from watcherobot.distribution.events import ErrorCode, ProgressEvent
from watcherobot.distribution.ports import (
    HubRepositoryNotFound,
    RepositoryRevision,
)
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifestError,
)


SPACE_ID = "alice/WatcherRobot-com.example.demo"
COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40


def _write_application(root: Path, *, marker: str = "first") -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "com.example.demo",
                "name": "Demo",
                "version": "1.2.3",
                "requires_watcherobot": ">=1.0,<2.0",
                "dependencies": [],
                "description": "Download fixture",
                "author": "Developer",
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text(
        f"MARKER = {marker!r}\n",
        encoding="utf-8",
    )
    nested = root / "source" / "feature.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("VALUE = 1\n", encoding="utf-8")


@dataclass
class FakeSnapshotHub:
    returned_commit: str = COMMIT
    failure: Exception | None = None
    marker: str = "first"
    calls: list[tuple[str, str, Path]] = field(default_factory=list)

    def download_space_snapshot(
        self,
        *,
        space_id: str,
        commit: str,
        target: Path,
    ) -> RepositoryRevision:
        self.calls.append((space_id, commit, target))
        if self.failure is not None:
            raise self.failure
        _write_application(target, marker=self.marker)
        return RepositoryRevision(
            commit=self.returned_commit,
            url=(
                f"https://huggingface.co/spaces/{space_id}/tree/"
                f"{self.returned_commit}"
            ),
        )


@dataclass
class RecordingEvents:
    events: list[ProgressEvent] = field(default_factory=list)

    def emit(self, event) -> None:
        assert isinstance(event, ProgressEvent)
        self.events.append(event)


@pytest.mark.parametrize("target_state", ["missing", "file", "non_empty"])
def test_download_requires_an_existing_empty_caller_target(
    tmp_path: Path,
    target_state: str,
) -> None:
    target = tmp_path / "caller-staging"
    if target_state == "file":
        target.write_text("not a directory", encoding="utf-8")
    elif target_state == "non_empty":
        target.mkdir()
        target.joinpath("keep.txt").write_text("keep", encoding="utf-8")
    hub = FakeSnapshotHub()

    with pytest.raises(DownloadError) as captured:
        download_application_snapshot(
            space_id=SPACE_ID,
            commit=COMMIT,
            target=target,
            hub=hub,
            watcherobot_version="1.5.0",
        )

    assert captured.value.code is ErrorCode.APP_CONTENT_FORBIDDEN
    assert hub.calls == []


def test_download_rejects_floating_revision_before_remote_call(
    tmp_path: Path,
) -> None:
    target = tmp_path / "caller-staging"
    target.mkdir()
    hub = FakeSnapshotHub()

    with pytest.raises(DownloadError) as captured:
        download_application_snapshot(
            space_id=SPACE_ID,
            commit="main",
            target=target,
            hub=hub,
            watcherobot_version="1.5.0",
        )

    assert captured.value.code is ErrorCode.CATALOG_INVALID
    assert hub.calls == []
    assert list(target.iterdir()) == []


def test_download_validates_in_isolation_then_delivers_exact_source(
    tmp_path: Path,
) -> None:
    target = tmp_path / "caller-staging"
    target.mkdir()
    hub = FakeSnapshotHub()
    events = RecordingEvents()

    result = download_application_snapshot(
        space_id=SPACE_ID,
        commit=COMMIT,
        target=target,
        hub=hub,
        events=events,
        watcherobot_version="1.5.0",
    )

    assert result.to_dict() == {
        "space_id": SPACE_ID,
        "commit": COMMIT,
        "source_url": (
            f"https://huggingface.co/spaces/{SPACE_ID}/tree/{COMMIT}"
        ),
        "target": str(target.resolve()),
        "application": {
            "schema_version": 1,
            "id": "com.example.demo",
            "name": "Demo",
            "version": "1.2.3",
            "requires_watcherobot": ">=1.0,<2.0",
            "dependencies": [],
            "description": "Download fixture",
            "author": "Developer",
            "icon": "",
        },
    }
    assert sorted(
        path.relative_to(target).as_posix() for path in target.rglob("*")
    ) == [
        "app.json",
        "app.py",
        "source",
        "source/feature.py",
    ]
    assert not target.joinpath("install.json").exists()
    assert [event.stage for event in events.events] == [
        "downloading_snapshot",
        "validating_snapshot",
        "delivering_snapshot",
    ]
    isolated_target = hub.calls[0][2]
    assert isolated_target != target
    assert not isolated_target.exists()


def test_download_excludes_hugging_face_local_metadata_from_delivered_source(
    tmp_path: Path,
) -> None:
    target = tmp_path / "caller-staging"
    target.mkdir()

    class MetadataHub(FakeSnapshotHub):
        def download_space_snapshot(self, **kwargs):
            revision = super().download_space_snapshot(**kwargs)
            metadata = kwargs["target"] / ".cache" / "huggingface"
            metadata.mkdir(parents=True)
            metadata.joinpath("app.py.metadata").write_text(
                "transport metadata",
                encoding="utf-8",
            )
            return revision

    download_application_snapshot(
        space_id=SPACE_ID,
        commit=COMMIT,
        target=target,
        hub=MetadataHub(),
        watcherobot_version="1.5.0",
    )

    assert target.joinpath("app.py").is_file()
    assert not target.joinpath(".cache", "huggingface").exists()


def test_wrong_resolved_commit_leaves_target_empty(tmp_path: Path) -> None:
    target = tmp_path / "caller-staging"
    target.mkdir()

    with pytest.raises(DownloadError) as captured:
        download_application_snapshot(
            space_id=SPACE_ID,
            commit=COMMIT,
            target=target,
            hub=FakeSnapshotHub(returned_commit=OTHER_COMMIT),
            watcherobot_version="1.5.0",
        )

    assert captured.value.code is ErrorCode.CATALOG_INVALID
    assert list(target.iterdir()) == []


def test_invalid_snapshot_manifest_leaves_target_empty(tmp_path: Path) -> None:
    target = tmp_path / "caller-staging"
    target.mkdir()

    class InvalidManifestHub(FakeSnapshotHub):
        def download_space_snapshot(self, **kwargs):
            revision = super().download_space_snapshot(**kwargs)
            kwargs["target"].joinpath("app.json").write_text(
                "{}",
                encoding="utf-8",
            )
            return revision

    with pytest.raises(ApplicationManifestError):
        download_application_snapshot(
            space_id=SPACE_ID,
            commit=COMMIT,
            target=target,
            hub=InvalidManifestHub(),
            watcherobot_version="1.5.0",
        )

    assert list(target.iterdir()) == []


def test_snapshot_manifest_id_must_match_space_name(tmp_path: Path) -> None:
    target = tmp_path / "caller-staging"
    target.mkdir()

    class DifferentApplicationHub(FakeSnapshotHub):
        def download_space_snapshot(self, **kwargs):
            revision = super().download_space_snapshot(**kwargs)
            manifest_path = kwargs["target"] / "app.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["id"] = "com.example.different"
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            return revision

    with pytest.raises(DownloadError) as captured:
        download_application_snapshot(
            space_id=SPACE_ID,
            commit=COMMIT,
            target=target,
            hub=DifferentApplicationHub(),
            watcherobot_version="1.5.0",
        )

    assert captured.value.code is ErrorCode.CATALOG_INVALID
    assert list(target.iterdir()) == []


def test_missing_space_is_sanitized_and_target_stays_empty(tmp_path: Path) -> None:
    target = tmp_path / "caller-staging"
    target.mkdir()

    with pytest.raises(DownloadError) as captured:
        download_application_snapshot(
            space_id=SPACE_ID,
            commit=COMMIT,
            target=target,
            hub=FakeSnapshotHub(
                failure=HubRepositoryNotFound("sensitive provider detail")
            ),
            watcherobot_version="1.5.0",
        )

    assert captured.value.code is ErrorCode.REMOTE_ERROR
    assert "sensitive provider detail" not in str(captured.value)
    assert list(target.iterdir()) == []


def test_same_fixed_commit_is_independent_from_later_main_changes(
    tmp_path: Path,
) -> None:
    first_target = tmp_path / "first"
    second_target = tmp_path / "second"
    first_target.mkdir()
    second_target.mkdir()
    hub = FakeSnapshotHub(marker="fixed")

    download_application_snapshot(
        space_id=SPACE_ID,
        commit=COMMIT,
        target=first_target,
        hub=hub,
        watcherobot_version="1.5.0",
    )
    hub.marker = "fixed"
    download_application_snapshot(
        space_id=SPACE_ID,
        commit=COMMIT,
        target=second_target,
        hub=hub,
        watcherobot_version="1.5.0",
    )

    assert first_target.joinpath("app.py").read_bytes() == second_target.joinpath(
        "app.py"
    ).read_bytes()
