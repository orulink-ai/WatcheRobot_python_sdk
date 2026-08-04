from __future__ import annotations

from pathlib import Path

import pytest

from watcherobot.distribution.source_files import (
    ApplicationSourceError,
    collect_application_source_files,
)


def test_source_file_set_excludes_local_and_generated_content(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "app.json", "{}")
    _write(tmp_path / "app.py", "print('demo')")
    _write(tmp_path / "src" / "feature.py", "VALUE = 1")
    _write(tmp_path / ".env", "HF_TOKEN=secret")
    _write(tmp_path / ".env.local", "PASSWORD=secret")
    _write(tmp_path / ".env.example", "TOKEN=")
    _write(tmp_path / ".venv" / "pyvenv.cfg", "home=C:/Python")
    _write(tmp_path / "venv" / "Scripts" / "python.exe", "binary")
    _write(tmp_path / "__pycache__" / "app.pyc", "cache")
    _write(tmp_path / ".pytest_cache" / "state", "cache")
    _write(tmp_path / ".mypy_cache" / "state", "cache")
    _write(tmp_path / "build" / "artifact.bin", "build")
    _write(tmp_path / "dist" / "demo.wapp", "package")
    _write(tmp_path / ".idea" / "workspace.xml", "local")
    _write(tmp_path / ".vscode" / "settings.json", "local")
    _write(tmp_path / "pyvenv.cfg", "home=C:/Python")
    _write(tmp_path / "debug.pth", "C:/local/source")
    _write(tmp_path / "Thumbs.db", "local")

    files = collect_application_source_files(tmp_path)

    assert [path.as_posix() for path in files] == [
        ".env.example",
        "app.json",
        "app.py",
        "src/feature.py",
    ]


def test_source_file_set_rejects_symlink_that_could_escape_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    outside = tmp_path / "outside.txt"
    source.mkdir()
    outside.write_text("outside", encoding="utf-8")
    try:
        source.joinpath("outside-link.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink is unavailable: {exc}")

    with pytest.raises(ApplicationSourceError) as captured:
        collect_application_source_files(source)

    assert captured.value.code == "app_content_forbidden"
    assert "symbolic link" in str(captured.value)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
