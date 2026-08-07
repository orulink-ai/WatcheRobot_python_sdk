"""Prepare one exact source snapshot for a public Application Space."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .check import ApplicationCheckResult
from .ports import UploadFile
from .source_files import (
    ApplicationSourceError,
    collect_application_source_files,
)


_FRONT_MATTER_DELIMITER = "---"
_TOP_LEVEL_KEY_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:")


def prepare_space_upload_files(
    application_dir: Path,
    application: ApplicationCheckResult,
) -> tuple[UploadFile, ...]:
    """Build a deterministic upload set without changing local source files."""

    root = Path(application_dir).resolve()
    selected = collect_application_source_files(root)
    uploads: list[UploadFile] = []
    has_root_readme = False

    for relative in selected:
        path_in_repo = relative.as_posix()
        if path_in_repo == "README.md":
            has_root_readme = True
            readme = _read_utf8(root / relative)
            uploads.append(
                UploadFile.from_bytes(
                    path_in_repo,
                    _with_static_space_metadata(readme, application).encode(
                        "utf-8"
                    ),
                )
            )
        else:
            uploads.append(
                UploadFile.from_path(path_in_repo, root / relative)
            )

    if not has_root_readme:
        uploads.append(
            UploadFile.from_bytes(
                "README.md",
                _generated_repository_readme(application).encode("utf-8"),
            )
        )

    return tuple(sorted(uploads, key=lambda item: item.path_in_repo))


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApplicationSourceError(
            "Application README.md must use UTF-8 encoding"
        ) from exc
    except OSError as exc:
        raise ApplicationSourceError(
            "Application README.md could not be read"
        ) from exc


def _with_static_space_metadata(
    readme: str,
    application: ApplicationCheckResult,
) -> str:
    normalized = readme.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != _FRONT_MATTER_DELIMITER:
        return _front_matter(application) + "\n" + normalized

    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.rstrip("\n") == _FRONT_MATTER_DELIMITER
        ),
        None,
    )
    if closing_index is None:
        raise ApplicationSourceError(
            "Application README.md has unclosed YAML front matter"
        )

    metadata_lines = [line.rstrip("\n") for line in lines[1:closing_index]]
    updated_metadata: list[str] = []
    replaced_sdk = False
    for line in metadata_lines:
        match = _TOP_LEVEL_KEY_PATTERN.match(line)
        if match is not None and match.group(1) == "sdk":
            if not replaced_sdk:
                updated_metadata.append("sdk: static")
                replaced_sdk = True
            continue
        updated_metadata.append(line)
    if not replaced_sdk:
        updated_metadata.append("sdk: static")

    body = "".join(lines[closing_index + 1 :])
    return (
        _FRONT_MATTER_DELIMITER
        + "\n"
        + "\n".join(updated_metadata)
        + "\n"
        + _FRONT_MATTER_DELIMITER
        + "\n"
        + body
    )


def _front_matter(application: ApplicationCheckResult) -> str:
    title = json.dumps(application.name, ensure_ascii=False)
    return f"---\ntitle: {title}\nsdk: static\n---\n"


def _generated_repository_readme(
    application: ApplicationCheckResult,
) -> str:
    sections = [
        _front_matter(application),
        f"# {application.name}\n",
    ]
    if application.description:
        sections.append(application.description + "\n")
    sections.append(
        "WatcherRobot Application source for "
        f"`{application.app_id}`.\n"
    )
    return "\n".join(sections)
