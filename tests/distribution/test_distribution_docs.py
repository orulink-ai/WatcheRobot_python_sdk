import re
from pathlib import Path

from watcherobot import __version__
from watcherobot.distribution.events import ErrorCode, ExitCode


ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = ROOT / "docs" / "application-marketplace"
CONTRACT_PATH = DOCS_ROOT / "distribution-contract.md"
INDEX_PATH = DOCS_ROOT / "README.md"
USAGE_GUIDE_PATH = DOCS_ROOT / "sdk-application-usage.md"
CHINESE_USAGE_GUIDE_PATH = DOCS_ROOT / "sdk-application-usage.zh-CN.md"
CLI_REFERENCE_PATH = DOCS_ROOT / "application-cli-reference.md"


def test_distribution_documentation_has_one_query_entrypoint() -> None:
    index = INDEX_PATH.read_text(encoding="utf-8")

    assert "distribution-contract.md" in index
    assert "implementation-progress.md" in index
    assert "hugging-face-oauth.md" in index
    assert "sdk-application-usage.md" in index
    assert "sdk-application-usage.zh-CN.md" in index
    assert "application-cli-reference.md" in index


def test_application_cli_reference_separates_human_and_machine_usage() -> None:
    reference = CLI_REFERENCE_PATH.read_text(encoding="utf-8")

    assert "For manual use, omit `--jsonl`" in reference
    assert "watcherobot app marketplace --details" in reference
    assert "watcherobot app marketplace --jsonl" in reference
    assert "Human-friendly English" in reference
    assert "Stable JSON Lines" in reference
    for command in (
        "check",
        "run",
        "login",
        "logout",
        "publish",
        "submit",
        "marketplace",
        "download",
        "install",
        "list",
        "uninstall",
        "start",
        "stop",
    ):
        assert f"`{command}`" in reference


def test_sdk_application_usage_guide_is_executable_and_matches_store_boundary() -> None:
    guide = USAGE_GUIDE_PATH.read_text(encoding="utf-8")

    assert f"`{__version__}`" in guide
    for command in (
        "app check",
        "app run",
        "app login --status",
        "app login --jsonl",
        "app publish",
        "app submit",
        "app marketplace --jsonl",
        "app download",
        "app install",
        "app list",
        "app uninstall",
        "app logout",
    ):
        assert f"watcherobot.exe {command}" in guide
    assert "ApplicationContext.from_environment()" in guide
    assert "Browser sign-in does not authenticate the SDK distribution tool" in guide
    assert "SDK owns download, installation, inventory, and removal" in guide
    assert "Do not run `app.py` directly" in guide
    assert "Open: https://hf.co/oauth/device" in guide
    assert "Use `--jsonl` only for Desktop or another machine caller" in guide


def test_chinese_usage_guide_directs_humans_to_interactive_english_login() -> None:
    guide = CHINESE_USAGE_GUIDE_PATH.read_text(encoding="utf-8")

    assert ".\\.venv\\Scripts\\watcherobot.exe app login\n" in guide
    assert "Authorize Hugging Face in your browser" in guide
    assert "Open: https://hf.co/oauth/device" in guide
    assert "Desktop 或其他机器调用方才使用 `--jsonl`" in guide


def test_readmes_describe_sdk_store_commands_but_not_daemon_selection() -> None:
    readmes = [
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (ROOT / "README.zh-CN.md").read_text(encoding="utf-8"),
    ]
    for document in readmes:
        assert "watcherobot app install" in document
        assert "watcherobot app list" in document
        assert "watcherobot app uninstall" in document
        assert re.search(r"(?m)^watcherobot app select\b", document) is None

    documents = [
        *readmes,
        (ROOT / "examples" / "README.md").read_text(encoding="utf-8"),
    ]
    for document in documents:
        assert re.search(
            r"(?m)^watcherobot app run .*\.wapp\s*$",
            document,
        ) is None


def test_distribution_contract_covers_current_version_and_commands() -> None:
    contract = CONTRACT_PATH.read_text(encoding="utf-8")

    assert f"`{__version__}`" in contract
    assert "`runtime-build.json` 的 `sdk_commit`" in contract
    assert not re.search(
        r"当前 Windows Desktop 随包 SDK 提交\s*\|\s*`[0-9a-f]{40}`",
        contract,
    )
    for command in (
        "check",
        "login",
        "logout",
        "publish",
        "submit",
        "marketplace",
        "download",
        "install",
        "list",
        "uninstall",
    ):
        assert f"`watcher-distribution app {command}" in contract


def test_distribution_contract_covers_all_stable_error_and_exit_codes() -> None:
    contract = CONTRACT_PATH.read_text(encoding="utf-8")

    for error_code in ErrorCode:
        assert f"`{error_code.value}`" in contract
    for exit_code in ExitCode:
        assert f"| `{exit_code.name}` | `{int(exit_code)}` |" in contract


def test_distribution_contract_keeps_daemon_and_hub_boundaries_explicit() -> None:
    contract = CONTRACT_PATH.read_text(encoding="utf-8")

    assert "Daemon 不访问 Hugging Face" in contract
    assert "固定 commit" in contract
    assert "不得读取 Hugging Face CLI" in contract
    assert "ASCII 安全的 Unicode 转义" in contract
    assert "不包含 Token、Device Code、时间戳或 traceback" in contract
