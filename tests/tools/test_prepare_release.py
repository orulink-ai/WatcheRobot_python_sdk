from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def _load_module():
    path = ROOT / "tools" / "prepare_release.py"
    spec = importlib.util.spec_from_file_location("prepare_release", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_prepare_release_updates_version_and_changelog(tmp_path: Path) -> None:
    module = _load_module()
    version_file = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"
    version_file.write_text('__version__ = "0.1.1a2"\n', encoding="utf-8")
    changelog.write_text("# 更新日志\n\n## [0.1.1a2] - 2026-08-07\n\n- 旧版本\n", encoding="utf-8")

    result = module.prepare_release(
        version_file=version_file,
        changelog_file=changelog,
        target="0.1.1a3",
        release_type=None,
        source="PR #37：增加实时视频",
    )

    assert result == "0.1.1a3"
    assert '__version__ = "0.1.1a3"' in version_file.read_text(encoding="utf-8")
    updated = changelog.read_text(encoding="utf-8")
    assert "## [0.1.1a3] - 待发布" in updated
    assert "PR #37：增加实时视频" in updated
    assert updated.index("0.1.1a3") < updated.index("0.1.1a2")


def test_prepare_release_calculates_release_type(tmp_path: Path) -> None:
    module = _load_module()
    version_file = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"
    version_file.write_text('__version__ = "0.1.1a2"\n', encoding="utf-8")
    changelog.write_text("# 更新日志\n", encoding="utf-8")

    result = module.prepare_release(
        version_file=version_file,
        changelog_file=changelog,
        target=None,
        release_type="prerelease",
        source="飞书指令",
    )

    assert result == "0.1.1a3"


def test_prepare_release_rejects_ambiguous_or_duplicate_requests(tmp_path: Path) -> None:
    module = _load_module()
    version_file = tmp_path / "__init__.py"
    changelog = tmp_path / "CHANGELOG.md"
    version_file.write_text('__version__ = "0.1.1a2"\n', encoding="utf-8")
    changelog.write_text("# 更新日志\n\n## [0.1.1a3] - 待发布\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one"):
        module.prepare_release(
            version_file=version_file,
            changelog_file=changelog,
            target="0.1.1a3",
            release_type="prerelease",
            source="ambiguous",
        )
    with pytest.raises(ValueError, match="already exists"):
        module.prepare_release(
            version_file=version_file,
            changelog_file=changelog,
            target="0.1.1a3",
            release_type=None,
            source="duplicate",
        )
