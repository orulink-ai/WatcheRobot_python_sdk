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


def test_release_workflow_separates_test_and_production_indexes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "tags: [\"v*\"]" in workflow
    assert "pull_request:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "environment: testpypi" in workflow
    assert "environment:\n      name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "https://test.pypi.org/legacy/" in workflow
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in workflow
    assert workflow.count("uv publish") == 2
    assert workflow.count("--trusted-publishing always") == 2
    assert "actions/upload-artifact@v7" in workflow
    assert workflow.count("actions/download-artifact@v8") >= 3
    assert "runs-on: [self-hosted, Linux, X64, sdk-release]" in workflow
    assert "tools/check_release_gate.py" in workflow
    assert 'tag_commit=$(git rev-list -n 1 "${GITHUB_REF_NAME}")' in workflow
    assert '--target "${{ needs.gate.outputs.commit }}"' in workflow
    assert "sha256sum dist/* > SHA256SUMS" in workflow
    assert "name: Clean release workspace before use" in workflow
    assert "name: Clean release workspace after use" in workflow
    assert "artifact/dist/*" in workflow
    assert "--index-url https://test.pypi.org/simple/" in workflow
    assert "--extra-index-url https://pypi.org/simple/" in workflow
    assert "gh release create" in workflow
    assert "--draft" in workflow
    assert "gh release edit" in workflow
    assert "PYPI_API_TOKEN" not in workflow
    assert "password:" not in workflow


def test_production_publish_requires_a_release_and_version_check() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "tools/check_release_version.py" in workflow
    gate = (ROOT / "tools" / "check_release_gate.py").read_text(encoding="utf-8")
    assert '"merge-base", "--is-ancestor"' in gate
    assert '"cat-file", "-t", tag' in gate
    assert "release ref must be an annotated tag" in gate
    assert "environment:\n      name: pypi" in workflow


def test_publish_workflow_tests_supported_dependency_profiles_before_one_build() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:\n    branches: [main]" in workflow
    assert "runs-on: [self-hosted, Linux, X64, sdk-ci]" in workflow
    assert 'python-version: ["3.10", "3.11", "3.12"]' in workflow
    assert 'dependency-profile: ["lowest", "latest"]' in workflow
    assert "python-version: ${{ matrix.python-version }}" in workflow
    assert '"fastapi==0.129.*"' in workflow
    assert '"huggingface-hub==1.26.*"' in workflow
    assert '"packaging==24.*"' in workflow
    assert '"uvicorn==0.30.*"' in workflow
    assert '"starlette==0.51.*"' in workflow
    assert '"websockets==14.*"' in workflow
    assert '"fastapi>=0.129,<1"' in workflow
    assert '"starlette>=0.51,<1"' in workflow
    assert '"websockets>=14,<16"' in workflow
    assert "python -m mypy src/watcherobot" in workflow
    assert "id-token: write" not in workflow
    assert "environment:" not in workflow
    assert "name: Build distributions" in workflow
    assert "needs: [test, ble-provisioning]" in workflow
    assert "python -m pip install --force-reinstall dist/*.whl" in workflow
    assert "python -m pip check" in workflow


def test_fake_ble_tests_run_on_self_hosted_linux() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")

    assert "ble-provisioning:" in workflow
    assert "runs-on: [self-hosted, Linux, X64, sdk-ci]" in workflow
    assert "python -m pytest tests/provisioning" in workflow
    assert "from watcherobot.provisioning.bleak_backend import BleakBackend" in workflow


def test_legacy_publish_workflow_is_removed() -> None:
    assert not (ROOT / ".github" / "workflows" / "publish.yml").exists()


def test_prepare_release_uses_repository_scoped_github_app() -> None:
    workflow = (ROOT / ".github" / "workflows" / "prepare-release.yml").read_text(
        encoding="utf-8"
    )

    assert "actions/create-github-app-token@v2" in workflow
    assert "ORULINK_RELEASE_APP_ID" in workflow
    assert "ORULINK_RELEASE_APP_PRIVATE_KEY" in workflow
    assert "token: ${{ steps.app-token.outputs.token }}" in workflow
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token }}" in workflow
    assert "workflow_dispatch:" in workflow
    assert "tools/check_release_availability.py" in workflow
    assert "runs-on: [self-hosted, Linux, X64, sdk-ci]" in workflow
    assert "runs-on: [self-hosted, Linux, X64, sdk-release]" not in workflow
