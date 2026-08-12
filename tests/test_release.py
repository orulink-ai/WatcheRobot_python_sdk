from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_package_version_has_one_release_source() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package_init = (ROOT / "src" / "watcherobot" / "__init__.py").read_text(encoding="utf-8")

    assert 'dynamic = ["version"]' in pyproject
    assert '[tool.hatch.version]\npath = "src/watcherobot/__init__.py"' in pyproject
    assert 'version = "0.1.0"' not in pyproject
    assert '__version__ = "0.1.1a3"' in package_init
    assert '"bleak>=3,<4"' in pyproject
    assert '"av>=16,<17"' in pyproject


def test_releasing_uses_one_next_patch_version_family() -> None:
    releasing = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    for version in ("0.1.1a1", "0.1.1b1", "0.1.1rc1", "0.1.1"):
        assert version in releasing
    assert "0.1.0b1" not in releasing
    assert "0.1.0rc1" not in releasing


def test_publish_workflow_separates_test_and_production_indexes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:\n    branches: [main]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "release:" in workflow
    assert "types: [published]" in workflow
    assert "environment: testpypi" in workflow
    assert "environment:\n      name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "https://test.pypi.org/legacy/" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert workflow.count("actions/download-artifact@v8") == 2
    assert "PYPI_API_TOKEN" not in workflow
    assert "password:" not in workflow


def test_production_publish_requires_a_release_and_version_check() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert "github.event_name == 'release'" in workflow
    assert "tools/check_release_version.py" in workflow
    assert "git merge-base --is-ancestor" in workflow


def test_publish_workflow_tests_supported_dependency_profiles_before_one_build() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert 'python-version: ["3.10", "3.11", "3.12"]' in workflow
    assert 'dependency-profile: ["lowest", "latest"]' in workflow
    assert "python-version: ${{ matrix.python-version }}" in workflow
    assert '"fastapi==0.129.*"' in workflow
    assert '"starlette==0.51.*"' in workflow
    assert '"websockets==14.*"' in workflow
    assert '"fastapi>=0.129,<1"' in workflow
    assert '"starlette>=0.51,<1"' in workflow
    assert '"websockets>=14,<16"' in workflow
    assert "python -m mypy src/watcherobot" in workflow
    assert "name: Build distributions" in workflow
    assert "needs: [test, ble-provisioning]" in workflow
    assert "python -m pip install --force-reinstall dist/*.whl" in workflow
    assert "python -m pip check" in workflow


def test_fake_ble_tests_run_on_windows_and_macos() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")

    assert "ble-provisioning:" in workflow
    assert "os: [windows-latest, macos-latest]" in workflow
    assert "python -m pytest tests/provisioning" in workflow
    assert "from watcherobot.provisioning.bleak_backend import BleakBackend" in workflow
