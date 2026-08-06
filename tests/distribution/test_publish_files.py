from __future__ import annotations

from pathlib import Path

import pytest

from watcherobot.distribution.check import ApplicationCheckResult
from watcherobot.distribution.publish_files import prepare_space_upload_files
from watcherobot.distribution.source_files import ApplicationSourceError


def _checked_app() -> ApplicationCheckResult:
    return ApplicationCheckResult(
        schema_version=1,
        app_id="com.orulink.demo",
        name="Demo Robot",
        version="1.2.3",
        requires_watcherobot=">=0.1,<0.2",
        dependencies=(),
        description="A robot demo",
        author="Developer",
        icon="",
    )


def test_existing_readme_body_is_preserved_and_only_remote_metadata_changes(
    tmp_path: Path,
) -> None:
    local_readme = (
        "---\n"
        "title: Developer title\n"
        "sdk: gradio\n"
        "tags:\n"
        "  - robot\n"
        "---\n\n"
        "# Developer documentation\n\n"
        "Keep this body exactly.\n"
    )
    _write(tmp_path / "app.json", "{}")
    _write(tmp_path / "app.py", "print('demo')\n")
    _write(tmp_path / "README.md", local_readme)

    files = prepare_space_upload_files(tmp_path, _checked_app())

    assert [item.path_in_repo for item in files] == [
        "README.md",
        "app.json",
        "app.py",
    ]
    remote_readme = _generated_content(files, "README.md").decode("utf-8")
    assert "title: Developer title" in remote_readme
    assert "sdk: static" in remote_readme
    assert "sdk: gradio" not in remote_readme
    assert "  - robot" in remote_readme
    assert remote_readme.endswith(
        "# Developer documentation\n\nKeep this body exactly.\n"
    )
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == local_readme
    assert not (tmp_path / "index.html").exists()


def test_missing_readme_creates_remote_repository_description_without_page(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "app.json", "{}")
    _write(tmp_path / "app.py", "print('demo')\n")
    _write(tmp_path / ".env", "TOKEN=secret\n")

    files = prepare_space_upload_files(tmp_path, _checked_app())

    assert [item.path_in_repo for item in files] == [
        "README.md",
        "app.json",
        "app.py",
    ]
    readme = _generated_content(files, "README.md").decode("utf-8")
    assert 'title: "Demo Robot"' in readme
    assert "sdk: static" in readme
    assert "# Demo Robot" in readme
    assert "A robot demo" in readme
    assert "`com.orulink.demo`" in readme
    assert "index.html" not in {item.path_in_repo for item in files}
    assert ".env" not in {item.path_in_repo for item in files}


def test_malformed_readme_frontmatter_is_rejected_without_local_mutation(
    tmp_path: Path,
) -> None:
    malformed = "---\ntitle: Broken\n# missing closing delimiter\n"
    _write(tmp_path / "app.json", "{}")
    _write(tmp_path / "app.py", "print('demo')\n")
    _write(tmp_path / "README.md", malformed)

    with pytest.raises(ApplicationSourceError, match="front matter") as error:
        prepare_space_upload_files(tmp_path, _checked_app())

    assert error.value.code == "app_content_forbidden"
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == malformed


def _generated_content(files, path_in_repo: str) -> bytes:
    matches = [item for item in files if item.path_in_repo == path_in_repo]
    assert len(matches) == 1
    content = matches[0].content
    assert content is not None
    return content


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
