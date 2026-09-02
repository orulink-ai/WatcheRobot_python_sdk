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
    version_sources = re.findall(
        r'^__version__ = "([^"]+)"$', package_init, flags=re.MULTILINE
    )
    assert len(version_sources) == 1
    assert str(Version(version_sources[0])) == version_sources[0]
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
    assert workflow.count("watcherobot-${{ needs.gate.outputs.version }}-${{ github.run_attempt }}") >= 4
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
    build_job = workflow.split("  build:", maxsplit=1)[1].split(
        "  draft-release:", maxsplit=1
    )[0]
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
    assert "--version-file \"${GITHUB_WORKSPACE}/src/watcherobot/__init__.py\"" in workflow
    assert "gh release download \"${RECOVER_TAG}\"" in workflow
    assert "tools/verify_release_artifacts.py" in workflow
    assert "https://test.pypi.org/pypi/watcherobot/${VERSION}/json" in workflow
    assert "actions/upload-artifact@v7" in recovery_draft_job
    assert "actions/download-artifact@v8" in recovery_publish_job
    assert "actions/download-artifact@v8" in recovery_verify_job
    assert "github.run_id" in recovery_gate_job
    assert "github.run_id" in recovery_publish_job
    assert "github.run_id" in recovery_verify_job
    assert "watcherobot-recovery-${{ steps.gate.outputs.version }}-${{ github.run_attempt }}" not in workflow
    assert "watcherobot-recovery-${{ needs.recover-gate.outputs.version }}-${{ github.run_attempt }}" not in workflow
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


def test_development_ci_uses_one_representative_python_environment() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")
    development = workflow.split("  development:", maxsplit=1)[1].split(
        "  release-compatibility:", maxsplit=1
    )[0]

    assert "pull_request:" in workflow
    assert "types: [opened, synchronize, reopened, labeled, unlabeled]" in workflow
    assert "push:\n    branches: [main]" in workflow
    assert "!contains(github.event.pull_request.labels.*.name, 'release:version')" in development
    assert "runs-on: [self-hosted, Linux, X64, sdk-ci]" in development
    assert 'python-version: "3.11"' in development
    assert "matrix:" not in development
    assert "dependency-profile" not in development
    assert development.count("actions/setup-python@v6") == 1
    assert "python -m pytest" in development
    assert "python -m mypy src/watcherobot" in development
    assert "python -m pytest tests/provisioning" in development
    assert "from watcherobot.provisioning.bleak_backend import BleakBackend" in development
    assert "id-token: write" not in workflow
    assert "environment:" not in workflow
    assert "python -m build" in workflow
    assert "python -m twine check dist/*" in workflow
    assert ".venv-wheel-check/bin/python -m pip install --force-reinstall dist/*.whl" in workflow
    assert "python -m pip check" in workflow
    assert development.count("name: Create isolated virtual environment") == 1
    assert 'echo "$PWD/.venv/bin" >> "$GITHUB_PATH"' in development
    assert development.count("api.github.com/repos/${GITHUB_REPOSITORY}/tarball/${GITHUB_SHA}") == 1
    assert development.count("tar --extract --gzip --strip-components=1") == 1
    assert development.count("name: Clean workspace before snapshot download") == 1
    assert development.count('workspace=$(realpath -m "${GITHUB_WORKSPACE}")') == 1
    assert development.count('runner_work=$(realpath -m "${RUNNER_TEMP}/..")') == 1
    assert development.count('[[ "${workspace}" == "${runner_work}"/*/* ]]') == 1
    assert "/opt/actions-runner-ci/_work" not in development
    assert development.count('rm -rf -- "${workspace}"/*') == 1
    assert "actions/checkout" not in development
    assert development.count("--retry 5 --retry-connrefused") == 1
    assert "--retry-all-errors" not in development
    assert "Building is deliberately independent of Git history and tags" in development
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'build-backend = "hatchling.build"' in pyproject
    assert '[tool.hatch.version]\npath = "src/watcherobot/__init__.py"' in pyproject
    assert "setuptools-scm" not in pyproject.lower()
    assert "versioneer" not in pyproject.lower()


def test_release_version_pr_runs_the_full_supported_compatibility_matrix() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")
    release = workflow.split("  release-compatibility:", maxsplit=1)[1]

    assert "contains(github.event.pull_request.labels.*.name, 'release:version')" in release
    assert 'python-version: ["3.10", "3.11", "3.12"]' in release
    assert 'dependency-profile: ["lowest", "latest"]' in release
    assert "max-parallel: 2" in release
    assert "python-version: ${{ matrix.python-version }}" in release
    assert '"fastapi==0.129.*"' in release
    assert '"huggingface-hub==1.26.*"' in release
    assert '"packaging==24.*"' in release
    assert '"uvicorn==0.30.*"' in release
    assert '"starlette==0.51.*"' in release
    assert '"websockets==14.*"' in release
    assert '"fastapi>=0.129,<1"' in release
    assert '"starlette>=0.51,<1"' in release
    assert '"websockets>=14,<16"' in release
    assert "python -m pytest" in release
    assert "python -m mypy src/watcherobot" in release
    assert "matrix.python-version == '3.12'" in release
    assert "matrix.dependency-profile == 'latest'" in release
    assert "python -m build" in release
    assert "python -m twine check dist/*" in release


def test_development_ci_pins_node_and_runs_media_browser_contracts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")
    development = workflow.split("  development:", maxsplit=1)[1].split(
        "  release-compatibility:", maxsplit=1
    )[0]

    assert (
        "uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4"
        in development
    )
    assert 'node-version: "22.14.0"' in development
    assert "name: Validate SDK media and vision browser helpers" in development
    assert "node --test tests/js/*.mjs" in development


def test_fake_ble_tests_run_in_development_ci_on_self_hosted_linux() -> None:
    workflow = (ROOT / ".github" / "workflows" / "sdk-ci.yml").read_text(encoding="utf-8")

    assert "runs-on: [self-hosted, Linux, X64, sdk-ci]" in workflow
    assert "python -m pytest tests/provisioning" in workflow
    assert "from watcherobot.provisioning.bleak_backend import BleakBackend" in workflow


def test_luxiao_review_uses_job_scoped_temporary_files() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pr-review.yml").read_text(encoding="utf-8")

    assert "${RUNNER_TEMP}/pr-${{ github.event.pull_request.number }}.diff" in workflow
    assert "${RUNNER_TEMP}/review_result.md" in workflow
    assert "/tmp/pr.diff" not in workflow
    assert "/tmp/review_result.md" not in workflow
    assert "PR_BODY: ${{ github.event.pull_request.body }}" in workflow
    assert '"${{ github.event.pull_request.body }}" \\' not in workflow
    bridge = (ROOT / ".github" / "scripts" / "luxiao_review.py").read_text(encoding="utf-8")
    assert "os.environ.get('PR_BODY', '')" in bridge
    assert "NamedTemporaryFile" in bridge
    assert '"LUXIAO_REMOTE_DIR", "/home/hermesadmin/.cache/luxiao-review"' in bridge
    assert 'local_file = "/tmp/luxiao_prompt.txt"' not in bridge
    assert 'MAX_DIFF_CHARS = 100_000' in bridge
    assert 'REMOTE_REVIEW_TIMEOUT_SECONDS = 600' in bridge
    assert '"timeout",' in bridge
    assert '"--kill-after=30s",' in bridge
    assert "timeout=REMOTE_REVIEW_TIMEOUT_SECONDS + 60" in bridge
    assert 'os.environ.get("LUXIAO_HERMES_HOST", "")' in bridge
    assert "LUXIAO_HERMES_HOST: ${{ vars.LUXIAO_HERMES_HOST }}" in workflow
    assert 'HERMES_HOST = "hermesadmin@192.168.1.116"' not in bridge
    assert "审查结论必须注明未覆盖范围" in bridge
    assert "runs-on: [self-hosted, Linux, X64, sdk-ci, pr-review]" in workflow
    assert "gh pr comment ${{ github.event.pull_request.number }} \\" in workflow
    assert "-R ${{ github.repository }} \\" in workflow


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
    assert "runs-on: [self-hosted, Linux, X64, sdk-orchestrator]" in workflow
    assert "runs-on: [self-hosted, Linux, X64, sdk-release]" not in workflow
    assert '--state open' in workflow
