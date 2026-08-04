"""Strict official-catalog updates and idempotent pull-request planning."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .ports import CatalogDocument, CatalogPullRequest


_FULL_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SPACE_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$"
)
_PULL_REQUEST_TITLE_PREFIX = "WatcherRobot catalog: "


class CatalogDocumentError(RuntimeError):
    """The public catalog cannot be safely updated."""

    code = "catalog_invalid"


class CatalogPullRequestConflict(RuntimeError):
    """Another commit for the same Space already has an open request."""

    code = "catalog_pr_conflict"

    def __init__(self, pull_request: CatalogPullRequest) -> None:
        self.pull_request = pull_request
        super().__init__(
            "An open catalog pull request already exists for another commit: "
            f"{pull_request.url}"
        )


@dataclass(frozen=True)
class CatalogEntry:
    """The only two fields accepted in the official V1 catalog."""

    space_id: str
    commit: str

    def to_dict(self) -> dict[str, str]:
        return {"space_id": self.space_id, "commit": self.commit}


def validate_catalog_reference(space_id: object, commit: object) -> CatalogEntry:
    """Validate one immutable public Space reference from any caller."""

    if (
        not isinstance(space_id, str)
        or _SPACE_ID_PATTERN.fullmatch(space_id) is None
    ):
        raise CatalogDocumentError("Catalog entry contains an invalid space_id")
    if (
        not isinstance(commit, str)
        or _FULL_COMMIT_PATTERN.fullmatch(commit) is None
    ):
        raise CatalogDocumentError(
            "Catalog entry commit must be a full lowercase SHA"
        )
    return CatalogEntry(space_id=space_id, commit=commit)


@dataclass(frozen=True)
class CatalogSubmissionPlan:
    """One network-free decision for the catalog submission step."""

    status: str
    parent_commit: str
    content: bytes | None = None
    pull_request: CatalogPullRequest | None = None


def catalog_pull_request_title(space_id: str, commit: str) -> str:
    """Return the stable title used to identify one pending commit."""

    return f"{_PULL_REQUEST_TITLE_PREFIX}{space_id}@{commit}"


def plan_catalog_submission(
    document: CatalogDocument,
    *,
    open_pull_requests: tuple[CatalogPullRequest, ...],
    space_id: str,
    commit: str,
) -> CatalogSubmissionPlan:
    """Plan an idempotent main-list update without mutating remote state."""

    entries = parse_catalog_entries(document.content)
    current = next(
        (entry for entry in entries if entry.space_id == space_id),
        None,
    )
    if current is not None and current.commit == commit:
        return CatalogSubmissionPlan(
            status="already_listed",
            parent_commit=document.commit,
        )

    matching_pull_requests = tuple(
        pull_request
        for pull_request in open_pull_requests
        if pull_request.title.startswith(
            f"{_PULL_REQUEST_TITLE_PREFIX}{space_id}@"
        )
    )
    if len(matching_pull_requests) > 1:
        raise CatalogPullRequestConflict(matching_pull_requests[0])
    if matching_pull_requests:
        existing = matching_pull_requests[0]
        if existing.title == catalog_pull_request_title(space_id, commit):
            return CatalogSubmissionPlan(
                status="pending",
                parent_commit=document.commit,
                pull_request=existing,
            )
        raise CatalogPullRequestConflict(existing)

    updated = list(entries)
    if current is None:
        updated.append(CatalogEntry(space_id=space_id, commit=commit))
    else:
        current_index = updated.index(current)
        updated[current_index] = CatalogEntry(
            space_id=space_id,
            commit=commit,
        )
    encoded = (
        json.dumps(
            [entry.to_dict() for entry in updated],
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    return CatalogSubmissionPlan(
        status="create",
        parent_commit=document.commit,
        content=encoded,
    )


def parse_catalog_entries(content: bytes) -> tuple[CatalogEntry, ...]:
    """Decode the strict public V1 catalog without performing network I/O."""

    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogDocumentError(
            "Official Application catalog is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(payload, list):
        raise CatalogDocumentError(
            "Official Application catalog must be a JSON array"
        )

    entries: list[CatalogEntry] = []
    seen_space_ids: set[str] = set()
    for item in payload:
        if not isinstance(item, dict) or set(item) != {"space_id", "commit"}:
            raise CatalogDocumentError(
                "Each catalog entry must contain only space_id and commit"
            )
        entry = validate_catalog_reference(item["space_id"], item["commit"])
        if entry.space_id in seen_space_ids:
            raise CatalogDocumentError(
                f"Catalog contains duplicate space_id: {entry.space_id}"
            )
        seen_space_ids.add(entry.space_id)
        entries.append(entry)
    return tuple(entries)
