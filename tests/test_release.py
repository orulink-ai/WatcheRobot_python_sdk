import re
from pathlib import Path

from packaging.version import Version


ROOT = Path(__file__).parents[1]


def test_package_version_has_one_release_source() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    package_init = (ROOT / "src" / "watcherobot" / "__init__.py").read_text(encoding="utf-8")

    assert 'dynamic = ["version"]' in pyproject
    assert '[tool.hatch.version]\npath = "src/watcherobot/__init__.py"' in pyproject
    assert 'version = "0.1.0"' not in pyproject
    version_sources = re.findall(r'^__version__ = "([^"]+)"$', package_init, flags=re.MULTILINE)
    assert len(version_sources) == 1
    assert str(Version(version_sources[0])) == version_sources[0]
    assert '"bleak>=3,<4"' in pyproject
    assert 'requires = ["hatchling>=1.24,<1.32"]' in pyproject
    assert '"av>=16,<17"' in pyproject


def test_releasing_uses_one_next_patch_version_family() -> None:
    releasing = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    for version in ("0.1.1a1", "0.1.1b1", "0.1.1rc1", "0.1.1"):
        assert version in releasing
    assert "0.1.0b1" not in releasing
    assert "0.1.0rc1" not in releasing


def test_release_workflow_separates_test_and_production_indexes() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert 'tags: ["v*"]' in workflow
    assert "pull_request:" not in workflow
    assert "workflow_dispatch:" in workflow
    assert "recover_tag:" in workflow
    assert "environment: testpypi" in workflow
    assert "environment:\n      name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "https://test.pypi.org/legacy/" in workflow
    assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in workflow
    assert workflow.count("uv publish") == 3
    assert workflow.count("--trusted-publishing always") == 3
    assert "UV_PUBLISH_CHECK_URL: https://test.pypi.org/simple/" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert workflow.count("actions/download-artifact@v8") >= 3
    assert (
        workflow.count("watcherobot-${{ needs.gate.outputs.version }}-${{ github.run_attempt }}")
        >= 4
    )
    assert "runs-on: [self-hosted, Linux, X64, sdk-release]" in workflow
    assert "tools/check_release_gate.py" in workflow
    assert 'tag_commit=$(git rev-list -n 1 "${GITHUB_REF_NAME}")' in workflow
    assert '--target "${{ needs.gate.outputs.commit }}"' in workflow
    assert "sha256sum dist/* > SHA256SUMS" in workflow
    assert "name: Clean release workspace before use" in workflow
    assert "name: Clean release workspace after use" in workflow
    assert "artifact/dist/*" in workflow
    assert "--index-url https://test.pypi.org/simple/" in workflow
    assert "--extra-index-url https://pypi.org/simple/" not in workflow
    assert "--no-deps --only-binary=:all:" in workflow
    assert "needs.gate.outputs.prerelease == 'false'" in workflow
    assert "needs.gate.outputs.prerelease == 'true'" in workflow
    assert "finalize-prerelease:" in workflow
    assert "gh release create" in workflow
    assert "--draft" in workflow
    assert "gh release edit" in workflow
    assert "PYPI_API_TOKEN" not in workflow
    assert "password:" not in workflow


def test_release_workflow_has_a_fail_closed_production_recovery_path() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    gate_job = workflow.split("  gate:", maxsplit=1)[1].split("  build:", maxsplit=1)[0]
    build_job = workflow.split("  build:", maxsplit=1)[1].split("  draft-release:", maxsplit=1)[0]
    recovery_gate_job = workflow.split("  recover-gate:", maxsplit=1)[1].split(
        "  recover-draft-assets:", maxsplit=1
    )[0]
    recovery_draft_job = workflow.split("  recover-draft-assets:", maxsplit=1)[1].split(
        "  recover-publish-pypi:", maxsplit=1
    )[0]
    recovery_publish_job = workflow.split("  recover-publish-pypi:", maxsplit=1)[1].split(
        "  recover-verify-pypi:", maxsplit=1
    )[0]
    recovery_verify_job = workflow.split("  recover-verify-pypi:", maxsplit=1)[1].split(
        "  recover-finish-clean:", maxsplit=1
    )[0]

    assert "recover-gate:" in workflow
    assert "recover-draft-assets:" in workflow
    assert "recover-publish-pypi:" in workflow
    assert "recover-verify-pypi:" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "github.event_name == 'push'" in workflow
    assert "inputs.recover_tag || github.ref_name" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert '[[ "${GITHUB_REF}" == "refs/heads/main" ]]' in workflow
    assert "ref: refs/tags/${{ inputs.recover_tag }}" in recovery_gate_job
    assert "--defer-draft-validation" in recovery_gate_job
    assert 'sys.exit("Production recovery requires a stable version")' in recovery_gate_job
    assert 'raise SystemExit("Production recovery requires a stable version")' not in workflow
    assert '--version-file "${GITHUB_WORKSPACE}/src/watcherobot/__init__.py"' in workflow
    assert 'gh release download "${RECOVER_TAG}"' in workflow
    assert "tools/verify_release_artifacts.py" in workflow
    assert "https://test.pypi.org/pypi/watcherobot/${VERSION}/json" in workflow
    assert "actions/upload-artifact@v7" in recovery_draft_job
    assert "actions/download-artifact@v8" in recovery_publish_job
    assert "actions/download-artifact@v8" in recovery_verify_job
    assert "github.run_id" in recovery_gate_job
    assert "github.run_id" in recovery_publish_job
    assert "github.run_id" in recovery_verify_job
    assert (
        "watcherobot-recovery-${{ steps.gate.outputs.version }}-${{ github.run_attempt }}"
        not in workflow
    )
    assert (
        "watcherobot-recovery-${{ needs.recover-gate.outputs.version }}-${{ github.run_attempt }}"
        not in workflow
    )
    assert 'mapfile -t distributions < <(python "${RECOVERY_VALIDATOR}"' in workflow
    assert 'uv publish --trusted-publishing always "${distributions[@]}"' in workflow
    assert "UV_PUBLISH_CHECK_URL: https://pypi.org/simple/" in workflow
    assert "environment:\n      name: pypi" in workflow
    assert 'gh release edit "${RECOVER_TAG}"' in workflow
    assert "contents: read" in gate_job
    assert "contents: write" not in gate_job
    assert "contents: read" in build_job
    assert "contents: write" not in build_job
    assert "contents: read" in recovery_gate_job
    assert "contents: write" not in recovery_gate_job
    assert "contents: write" in recovery_draft_job
    assert "actions/checkout" not in recovery_draft_job
    assert "overwrite: true" in recovery_draft_job
    assert "needs: [recover-gate, recover-draft-assets]" in recovery_publish_job
    assert "contents: write" not in recovery_publish_job
    assert "id-token: write" in recovery_publish_job
    assert "--no-cache-dir" in workflow
    assert "--index-url https://pypi.org/simple/" in workflow
    assert "--force-reinstall" in recovery_verify_job
    assert "https://pypi.org/pypi/watcherobot/${VERSION}/json" in recovery_verify_job
    assert "--registry-name PyPI" in recovery_verify_job
    assert "RECOVERY_DIR_NAME:" in recovery_verify_job
    assert workflow.count("python -m pip install packaging==26.0") == 4


def test_production_publish_requires_a_release_and_version_check() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "tools/check_release_version.py" in workflow
    gate = (ROOT / "tools" / "check_release_gate.py").read_text(encoding="utf-8")
    assert '"merge-base", "--is-ancestor"' in gate
    assert '"cat-file", "-t", tag' in gate
    assert "release ref must be an annotated tag" in gate
    assert "environment:\n      name: pypi" in workflow


def test_development_ci_uses_hosted_runner_and_emits_evidence() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")
    development = workflow.split("  development:", maxsplit=1)[1].split(
        "  compatibility:", maxsplit=1
    )[0]

    assert "pull_request:" in workflow
    assert "push:\n    branches: [main]" in workflow
    assert "runs-on: ubuntu-24.04" in development
    assert "self-hosted" not in development
    assert 'python-version: "3.11"' in development
    assert "matrix:" not in development
    assert (
        development.count("actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6") == 1
    )
    assert "python -m pytest --junitxml=artifacts/pytest.xml" in development
    assert "python -m mypy src/watcherobot" in development
    assert "id-token: write" not in workflow
    assert "environment:" not in workflow
    assert "python -m build" in workflow
    assert "python -m twine check dist/*" in workflow
    assert ".venv-wheel-check/bin/python -m pip install --force-reinstall dist/*.whl" in workflow
    assert ".venv-wheel-check/bin/watcher-distribution --help" in workflow
    assert "python -m pip check" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in development
    assert "artifacts/SHA256SUMS" in development
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'build-backend = "hatchling.build"' in pyproject
    assert '[tool.hatch.version]\npath = "src/watcherobot/__init__.py"' in pyproject
    assert "setuptools-scm" not in pyproject.lower()
    assert "versioneer" not in pyproject.lower()


def test_every_pr_runs_supported_runtime_and_dependency_compatibility() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")
    compatibility = workflow.split("  compatibility:", maxsplit=1)[1].split(
        "  cross-platform:", maxsplit=1
    )[0]

    for version in ('"3.10"', '"3.11"', '"3.12"'):
        assert version in compatibility
    assert compatibility.count("dependency-profile: lowest") == 1
    assert compatibility.count("dependency-profile: latest") == 3
    assert "python-version: ${{ matrix.python-version }}" in compatibility
    assert '"fastapi==0.129.*"' in compatibility
    assert '"huggingface-hub==1.26.*"' in compatibility
    assert '"packaging==24.*"' in compatibility
    assert '"uvicorn==0.30.*"' in compatibility
    assert '"starlette==0.51.*"' in compatibility
    assert '"websockets==14.*"' in compatibility
    assert "python -m pytest --junitxml=" in compatibility


def test_development_ci_pins_node_and_runs_media_browser_contracts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")
    development = workflow.split("  development:", maxsplit=1)[1].split(
        "  compatibility:", maxsplit=1
    )[0]

    assert "uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4" in development
    assert 'node-version: "22.14.0"' in development
    assert "name: Run fixed-Node media and vision browser contracts" in development
    assert "node --test tests/js/*.mjs" in development


def test_hardware_ble_is_isolated_from_untrusted_pr_code() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")
    hil = (ROOT / ".github" / "workflows" / "hardware-hil.yml").read_text(encoding="utf-8")

    assert "self-hosted" not in workflow
    assert "workflow_dispatch:" in hil
    assert "runs-on: [self-hosted, Windows, X64, watcher-hil]" in hil
    assert "ble_provisioning_hardware_test.py" in hil
    assert "Read provisioning status without changing credentials" in hil
    assert "BLE_ID_PREFIX: ${{ inputs.ble_id_prefix }}" in hil
    assert "--id-prefix $env:BLE_ID_PREFIX" in hil
    assert " status `" in hil


def test_ci_covers_cross_platform_quality_security_and_sbom() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")
    security = (ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")

    assert "os: [ubuntu-latest, windows-latest, macos-latest]" in workflow
    assert 'PYTHONUTF8: "1"' in workflow
    assert "python -m ruff check" in workflow
    assert "python -m ruff format --check" in workflow
    assert "scanners: vuln,secret,license" in security
    assert "format: cyclonedx" in security
    assert "actions/dependency-review-action" not in security
    assert "id: upload-sbom" in security
    assert "continue-on-error: true" in security
    assert "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25" in security


def test_sdk_ci_actions_in_scope_use_immutable_commits() -> None:
    for filename in ("sdk-ci.yml", "hardware-hil.yml", "security.yml"):
        workflow = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        references = re.findall(r"\buses:\s+[^\s@]+@([^\s#]+)", workflow)
        assert references, filename
        assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in references)


def test_luxiao_review_uses_job_scoped_temporary_files() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pr-review.yml").read_text(encoding="utf-8")

    assert "${RUNNER_TEMP}/pr-${{ github.event.pull_request.number }}.diff" in workflow
    assert "${RUNNER_TEMP}/review_result.md" in workflow
    assert "/tmp/pr.diff" not in workflow
    assert "/tmp/review_result.md" not in workflow
    assert "PR_BODY: ${{ github.event.pull_request.body }}" in workflow
    assert '"${{ github.event.pull_request.body }}" \\' not in workflow
    assert (
        '"repos/${{ github.repository }}/contents/.github/scripts/luxiao_review.py?ref=${BASE_SHA}"'
        in workflow
    )
    assert 'python3 "${REVIEW_SCRIPT}" \\' in workflow
    assert "BASE_SHA: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "/home/runner-ci/scripts/luxiao-review.py" not in workflow
    bridge = (ROOT / ".github" / "scripts" / "luxiao_review.py").read_text(encoding="utf-8")
    assert "os.environ.get('PR_BODY', '')" in bridge
    assert "NamedTemporaryFile" in bridge
    assert '"LUXIAO_REMOTE_DIR", "/home/hermesadmin/.cache/luxiao-review"' in bridge
    assert 'local_file = "/tmp/luxiao_prompt.txt"' not in bridge
    assert "MAX_DIFF_CHARS = 100_000" in bridge
    assert "REMOTE_REVIEW_TIMEOUT_SECONDS = 600" in bridge
    assert '"timeout",' in bridge
    assert '"--kill-after=30s",' in bridge
    assert "timeout=REMOTE_REVIEW_TIMEOUT_SECONDS + 60" in bridge
    assert 'os.environ.get("LUXIAO_HERMES_HOST", "")' in bridge
    assert "LUXIAO_HERMES_HOST: ${{ vars.LUXIAO_HERMES_HOST }}" in workflow
    assert 'HERMES_HOST = "hermesadmin@192.168.1.116"' not in bridge
    assert "审查结论必须注明未覆盖范围" in bridge
    assert "runs-on: [self-hosted, Linux, X64, sdk-ci, pr-review, luxiao-hermes]" in workflow
    assert "gh pr comment ${{ github.event.pull_request.number }} \\" in workflow
    assert "for attempt in 1 2 3; do" in workflow
    assert "missing_review=1" in workflow
    assert "审查结果为空，已发布故障评论并保留门禁失败状态" in workflow
    assert "连续 3 次发布审查评论失败，保留门禁失败状态" in workflow
    assert "-R ${{ github.repository }} \\" in workflow


def test_legacy_publish_workflow_is_removed() -> None:
    assert not (ROOT / ".github" / "workflows" / "publish.yml").exists()


def test_prepare_release_uses_repository_scoped_github_app() -> None:
    workflow = (ROOT / ".github" / "workflows" / "prepare-release.yml").read_text(encoding="utf-8")

    assert "actions/create-github-app-token@v2" in workflow
    assert "ORULINK_RELEASE_APP_ID" in workflow
    assert "ORULINK_RELEASE_APP_PRIVATE_KEY" in workflow
    assert "token: ${{ steps.app-token.outputs.token }}" in workflow
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token }}" in workflow
    assert "workflow_dispatch:" in workflow
    assert "tools/check_release_availability.py" in workflow
    assert "runs-on: [self-hosted, Linux, X64, sdk-orchestrator]" in workflow
    assert "runs-on: [self-hosted, Linux, X64, sdk-release]" not in workflow
    assert "--state open" in workflow
