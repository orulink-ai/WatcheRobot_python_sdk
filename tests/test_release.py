from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_package_version_has_one_alpha_source() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package_init = (ROOT / "src" / "watcherobot" / "__init__.py").read_text(encoding="utf-8")

    assert 'dynamic = ["version"]' in pyproject
    assert '[tool.hatch.version]\npath = "src/watcherobot/__init__.py"' in pyproject
    assert 'version = "0.1.0"' not in pyproject
    assert '__version__ = "0.1.0a1"' in package_init


def test_publish_workflow_separates_test_and_production_indexes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "release:" in workflow
    assert "types: [published]" in workflow
    assert "environment: testpypi" in workflow
    assert "environment:\n      name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "https://test.pypi.org/legacy/" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "PYPI_API_TOKEN" not in workflow
    assert "password:" not in workflow


def test_production_publish_requires_a_release_and_version_check() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert "github.event_name == 'release'" in workflow
    assert "tools/check_release_version.py" in workflow
    assert "git merge-base --is-ancestor" in workflow
