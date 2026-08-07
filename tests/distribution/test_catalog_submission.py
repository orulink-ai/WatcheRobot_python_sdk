from __future__ import annotations

import json

import pytest

from watcherobot.distribution.catalog_submission import (
    CatalogDocumentError,
    CatalogPullRequestConflict,
    catalog_pull_request_title,
    plan_catalog_submission,
)
from watcherobot.distribution.ports import (
    CatalogDocument,
    CatalogPullRequest,
)


SPACE_ID = "developer/WatcherRobot-com.orulink.demo"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
CATALOG_COMMIT = "c" * 40


def _document(entries: object) -> CatalogDocument:
    return CatalogDocument(
        content=(
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
        commit=CATALOG_COMMIT,
    )


def _pull_request(space_id: str, commit: str, number: int = 7):
    return CatalogPullRequest(
        number=number,
        title=catalog_pull_request_title(space_id, commit),
        url=(
            "https://huggingface.co/datasets/Orulink/"
            f"watcherobot-app-store/discussions/{number}"
        ),
        status="open",
    )


def test_first_submission_appends_minimal_entry() -> None:
    plan = plan_catalog_submission(
        _document([]),
        open_pull_requests=(),
        space_id=SPACE_ID,
        commit=COMMIT_A,
    )

    assert plan.status == "create"
    assert plan.parent_commit == CATALOG_COMMIT
    assert plan.pull_request is None
    assert json.loads(plan.content or b"") == [
        {"space_id": SPACE_ID, "commit": COMMIT_A}
    ]
    assert (plan.content or b"").endswith(b"\n")


def test_new_version_updates_commit_in_place_without_reordering() -> None:
    other = {
        "space_id": "someone/WatcherRobot-other",
        "commit": "d" * 40,
    }
    plan = plan_catalog_submission(
        _document(
            [
                {"space_id": SPACE_ID, "commit": COMMIT_A},
                other,
            ]
        ),
        open_pull_requests=(),
        space_id=SPACE_ID,
        commit=COMMIT_B,
    )

    assert json.loads(plan.content or b"") == [
        {"space_id": SPACE_ID, "commit": COMMIT_B},
        other,
    ]


def test_same_commit_already_on_main_is_idempotent() -> None:
    plan = plan_catalog_submission(
        _document([{"space_id": SPACE_ID, "commit": COMMIT_A}]),
        open_pull_requests=(_pull_request(SPACE_ID, COMMIT_B),),
        space_id=SPACE_ID,
        commit=COMMIT_A,
    )

    assert plan.status == "already_listed"
    assert plan.content is None
    assert plan.pull_request is None


def test_same_commit_open_pull_request_is_reused() -> None:
    existing = _pull_request(SPACE_ID, COMMIT_A)

    plan = plan_catalog_submission(
        _document([]),
        open_pull_requests=(existing,),
        space_id=SPACE_ID,
        commit=COMMIT_A,
    )

    assert plan.status == "pending"
    assert plan.content is None
    assert plan.pull_request is existing


def test_different_commit_open_pull_request_is_conflict() -> None:
    existing = _pull_request(SPACE_ID, COMMIT_A)

    with pytest.raises(CatalogPullRequestConflict) as captured:
        plan_catalog_submission(
            _document([]),
            open_pull_requests=(existing,),
            space_id=SPACE_ID,
            commit=COMMIT_B,
        )

    assert captured.value.code == "catalog_pr_conflict"
    assert captured.value.pull_request is existing
    assert existing.url in str(captured.value)


def test_open_pull_request_for_another_space_is_ignored() -> None:
    plan = plan_catalog_submission(
        _document([]),
        open_pull_requests=(
            _pull_request("developer/WatcherRobot-other", COMMIT_A),
        ),
        space_id=SPACE_ID,
        commit=COMMIT_A,
    )

    assert plan.status == "create"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        ["invalid"],
        [{"space_id": SPACE_ID}],
        [{"space_id": SPACE_ID, "commit": "main"}],
        [{"space_id": "not-a-space", "commit": COMMIT_A}],
        [
            {
                "space_id": SPACE_ID,
                "commit": COMMIT_A,
                "extra": True,
            }
        ],
        [
            {"space_id": SPACE_ID, "commit": COMMIT_A},
            {"space_id": SPACE_ID, "commit": COMMIT_B},
        ],
    ],
)
def test_invalid_or_conflicting_catalog_is_rejected(payload: object) -> None:
    with pytest.raises(CatalogDocumentError) as captured:
        plan_catalog_submission(
            _document(payload),
            open_pull_requests=(),
            space_id=SPACE_ID,
            commit=COMMIT_A,
        )

    assert captured.value.code == "catalog_invalid"


def test_catalog_must_be_utf8_json() -> None:
    document = CatalogDocument(content=b"\xff", commit=CATALOG_COMMIT)

    with pytest.raises(CatalogDocumentError):
        plan_catalog_submission(
            document,
            open_pull_requests=(),
            space_id=SPACE_ID,
            commit=COMMIT_A,
        )
