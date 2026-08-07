from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from watcherobot.distribution.events import ErrorCode, ProgressEvent
from watcherobot.distribution.marketplace import (
    MarketplaceError,
    load_official_marketplace,
)
from watcherobot.distribution.ports import CatalogDocument, HubNetworkError


CATALOG_COMMIT = "c" * 40
FIRST_COMMIT = "a" * 40
SECOND_COMMIT = "b" * 40
FIRST_SPACE = "alice/WatcherRobot-com.example.first"
SECOND_SPACE = "bob/WatcherRobot-com.example.second"


def _manifest(
    app_id: str,
    *,
    name: str,
    requires_watcherobot: str = ">=1.0,<2.0",
    extra: dict[str, object] | None = None,
) -> bytes:
    payload: dict[str, object] = {
        "schema_version": 1,
        "id": app_id,
        "name": name,
        "version": "1.2.3",
        "requires_watcherobot": requires_watcherobot,
        "dependencies": ["httpx>=0.28,<1"],
        "description": f"{name} description",
        "author": "Developer",
        "icon": "",
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload).encode("utf-8")


@dataclass
class FakeMarketplaceHub:
    catalog: CatalogDocument
    manifests: dict[tuple[str, str], bytes] = field(default_factory=dict)
    failure: str = ""
    calls: list[tuple[str, object]] = field(default_factory=list)

    def read_public_catalog(self, *, repo_id: str, path: str) -> CatalogDocument:
        self.calls.append(("catalog", (repo_id, path)))
        if self.failure == "catalog":
            raise HubNetworkError("network unavailable")
        return self.catalog

    def read_space_file(
        self,
        *,
        space_id: str,
        commit: str,
        path: str,
    ) -> bytes:
        self.calls.append(("file", (space_id, commit, path)))
        if self.failure == space_id:
            raise HubNetworkError("space unavailable")
        return self.manifests[(space_id, commit)]


@dataclass
class RecordingEvents:
    events: list[ProgressEvent] = field(default_factory=list)

    def emit(self, event) -> None:
        assert isinstance(event, ProgressEvent)
        self.events.append(event)


def _catalog(entries: object) -> CatalogDocument:
    return CatalogDocument(
        content=json.dumps(entries).encode("utf-8"),
        commit=CATALOG_COMMIT,
    )


def test_empty_official_catalog_needs_no_login_or_space_reads() -> None:
    hub = FakeMarketplaceHub(catalog=_catalog([]))
    events = RecordingEvents()

    result = load_official_marketplace(
        hub=hub,
        events=events,
        watcherobot_version="1.5.0",
    )

    assert result.to_dict() == {
        "catalog_commit": CATALOG_COMMIT,
        "applications": [],
    }
    assert hub.calls == [
        (
            "catalog",
            ("Orulink/watcherobot-app-store", "app-list.json"),
        )
    ]
    assert [event.stage for event in events.events] == ["fetching_catalog"]


def test_official_catalog_reads_each_manifest_at_its_fixed_commit() -> None:
    hub = FakeMarketplaceHub(
        catalog=_catalog(
            [
                {"space_id": FIRST_SPACE, "commit": FIRST_COMMIT},
                {"space_id": SECOND_SPACE, "commit": SECOND_COMMIT},
            ]
        ),
        manifests={
            (FIRST_SPACE, FIRST_COMMIT): _manifest(
                "com.example.first",
                name="First",
            ),
            (SECOND_SPACE, SECOND_COMMIT): _manifest(
                "com.example.second",
                name="Second",
                requires_watcherobot=">=2.0,<3.0",
            ),
        },
    )

    result = load_official_marketplace(
        hub=hub,
        watcherobot_version="1.5.0",
    )

    assert result.catalog_commit == CATALOG_COMMIT
    assert [app.app_id for app in result.applications] == [
        "com.example.first",
        "com.example.second",
    ]
    assert [app.compatible for app in result.applications] == [True, False]
    first = result.applications[0].to_dict()
    assert first == {
        "space_id": FIRST_SPACE,
        "commit": FIRST_COMMIT,
        "source_url": (
            f"https://huggingface.co/spaces/{FIRST_SPACE}/tree/{FIRST_COMMIT}"
        ),
        "schema_version": 1,
        "id": "com.example.first",
        "name": "First",
        "version": "1.2.3",
        "requires_watcherobot": ">=1.0,<2.0",
        "dependencies": ["httpx>=0.28,<1"],
        "description": "First description",
        "author": "Developer",
        "icon": "",
        "compatible": True,
    }
    assert hub.calls[1:] == [
        ("file", (FIRST_SPACE, FIRST_COMMIT, "app.json")),
        ("file", (SECOND_SPACE, SECOND_COMMIT, "app.json")),
    ]


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"{}",
        json.dumps(
            [
                {"space_id": FIRST_SPACE, "commit": FIRST_COMMIT},
                {"space_id": FIRST_SPACE, "commit": SECOND_COMMIT},
            ]
        ).encode("utf-8"),
        json.dumps(
            [{"space_id": FIRST_SPACE, "commit": "main"}]
        ).encode("utf-8"),
    ],
)
def test_invalid_official_catalog_stops_before_space_reads(content: bytes) -> None:
    hub = FakeMarketplaceHub(
        catalog=CatalogDocument(content=content, commit=CATALOG_COMMIT)
    )

    with pytest.raises(MarketplaceError) as captured:
        load_official_marketplace(hub=hub, watcherobot_version="1.5.0")

    assert captured.value.code is ErrorCode.CATALOG_INVALID
    assert [name for name, _ in hub.calls] == ["catalog"]


def test_invalid_fixed_manifest_has_catalog_error_with_source_details() -> None:
    hub = FakeMarketplaceHub(
        catalog=_catalog(
            [{"space_id": FIRST_SPACE, "commit": FIRST_COMMIT}]
        ),
        manifests={
            (FIRST_SPACE, FIRST_COMMIT): _manifest(
                "com.example.first",
                name="First",
                extra={"unknown": "field"},
            )
        },
    )

    with pytest.raises(MarketplaceError) as captured:
        load_official_marketplace(hub=hub, watcherobot_version="1.5.0")

    assert captured.value.code is ErrorCode.CATALOG_INVALID
    assert captured.value.details == {
        "space_id": FIRST_SPACE,
        "commit": FIRST_COMMIT,
    }


def test_manifest_id_must_match_the_reviewed_space_name() -> None:
    hub = FakeMarketplaceHub(
        catalog=_catalog(
            [{"space_id": FIRST_SPACE, "commit": FIRST_COMMIT}]
        ),
        manifests={
            (FIRST_SPACE, FIRST_COMMIT): _manifest(
                "com.example.different",
                name="Different",
            )
        },
    )

    with pytest.raises(MarketplaceError) as captured:
        load_official_marketplace(hub=hub, watcherobot_version="1.5.0")

    assert captured.value.code is ErrorCode.CATALOG_INVALID
    assert captured.value.details["id"] == "com.example.different"


@pytest.mark.parametrize("failure", ["catalog", FIRST_SPACE])
def test_public_hub_failure_has_stable_remote_error(failure: str) -> None:
    hub = FakeMarketplaceHub(
        catalog=_catalog(
            [{"space_id": FIRST_SPACE, "commit": FIRST_COMMIT}]
        ),
        manifests={
            (FIRST_SPACE, FIRST_COMMIT): _manifest(
                "com.example.first",
                name="First",
            )
        },
        failure=failure,
    )

    with pytest.raises(MarketplaceError) as captured:
        load_official_marketplace(hub=hub, watcherobot_version="1.5.0")

    assert captured.value.code is ErrorCode.REMOTE_ERROR
    assert "network unavailable" not in str(captured.value)
