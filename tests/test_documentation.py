from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_readmes_document_managed_application_and_runtime_cli() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")

        assert "ApplicationContext.from_environment()" in readme
        assert "app.robot" in readme
        assert "app.desktop" in readme
        assert "app.logger" in readme
        assert "watcherobot app run" in readme
        assert "watcherobot daemon start" in readme
        assert "watcherobot --version" in readme
        assert "watcherobot robot setup" in readme
        assert "watcherobot robot status" in readme
        assert "watcherobot app package" not in readme
        assert "WatcheRobot.connect" not in readme
        assert "BackgroundTransport" not in readme
        assert "SDK_DISCOVER" not in readme


def test_onboarding_docs_explain_the_guided_robot_setup_flow() -> None:
    documents = (
        ROOT / "README.md",
        ROOT / "README.zh-CN.md",
        ROOT / "docs" / "cli-reference.md",
        ROOT / "docs" / "cli-reference.zh-CN.md",
    )

    for path in documents:
        content = path.read_text(encoding="utf-8")
        assert "Settings > Wi-Fi" in content
        assert "Device ID" in content
        assert "Bluetooth ID" in content
        assert "Up/Down" in content
        assert '"Python SDK"' in content
        assert "watcherobot robot pair" in content


def test_cli_references_cover_robot_setup_recovery_states() -> None:
    references = (
        ROOT / "docs" / "cli-reference.md",
        ROOT / "docs" / "cli-reference.zh-CN.md",
    )

    for path in references:
        content = path.read_text(encoding="utf-8")
        assert "Bluetooth" in content
        assert "Device ID" in content
        assert "permission" in content or "权限" in content
        assert "timeout" in content or "超时" in content
        assert "cancel" in content or "取消" in content


def test_cli_references_explain_automatic_stable_application_ids() -> None:
    references = (
        ROOT / "docs" / "cli-reference.md",
        ROOT / "docs" / "cli-reference.zh-CN.md",
    )

    for path in references:
        content = path.read_text(encoding="utf-8")
        assert "local.my_app" in content
        assert "Application ID:" in content
        assert "where.exe watcherobot" in content
        assert "timestamp" in content or "时间戳" in content


def test_installation_guides_separate_source_and_release_installation() -> None:
    guides = (
        ROOT / "docs" / "installation.md",
        ROOT / "docs" / "installation.zh-CN.md",
    )

    for path in guides:
        content = path.read_text(encoding="utf-8")
        assert "conda create -n watcherobot python=3.11" in content
        assert "conda create -n watcherobot-source python=3.11" in content
        assert "python -m pip install watcherobot" in content
        assert "python -m pip install -e ." in content
        assert 'python -m pip install -e ".[test]"' in content
        assert "git switch --detach COMMIT_SHA" in content
        assert "--extra-index-url https://pypi.org/simple/" in content
        assert "where.exe watcherobot" in content
        assert "which watcherobot" in content

    english_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese_readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "docs/installation.md" in english_readme
    assert "docs/installation.zh-CN.md" in chinese_readme


def test_resource_and_troubleshooting_guides_match_runtime_ownership() -> None:
    resources = (ROOT / "docs" / "resources.md").read_text(encoding="utf-8")
    troubleshooting = (
        ROOT / "docs" / "troubleshooting.md"
    ).read_text(encoding="utf-8")

    for resource_id in (
        "happy",
        "smile",
        "blink",
        "breathing",
        "rainbow",
        "status_pulse",
    ):
        assert f"`{resource_id}`" in resources
    assert "ApplicationContext.robot" in resources
    assert "not_found" in resources
    assert "watcherobot daemon status" in troubleshooting
    assert "Application environment is incomplete" in troubleshooting
    assert "WatcheRobot.connect" not in troubleshooting


def test_release_guide_uses_the_single_package_version_source() -> None:
    guide = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    assert "`watcherobot.__version__`" in guide
    assert "watcherobot==<version>" in guide
    assert "文档不重复维护当前版本号" in guide


def test_release_guide_uses_managed_application_acceptance() -> None:
    guide = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    assert "tools/hardware_smoke.py" not in guide
    assert "tests/fakes/" not in guide
    assert "--no-deps" not in guide
    assert "--extra-index-url https://pypi.org/simple/" in guide
    assert "watcherobot app run" in guide


def test_readmes_document_current_desktop_and_standalone_support() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    normalized_english = " ".join(english.split())
    normalized_chinese = "".join(chinese.split())

    assert "desktop uses this same Runtime/Daemon implementation" in normalized_english
    assert "桌面端也使用同一份Runtime/Daemon实现" in normalized_chinese
    assert "/daemon/devices/pair" not in english
    assert "/daemon/devices/pair" not in chinese
    assert "GET /daemon/logs" in english
    assert "GET /daemon/logs" in chinese


def test_hardware_guide_contains_standalone_pairing_step() -> None:
    guide = (ROOT / "docs" / "hardware-testing.md").read_text(
        encoding="utf-8"
    )

    assert "watcherobot robot pair 123456" in guide
    assert "watcherobot robot status" in guide
    assert "/daemon/devices/pair" not in guide
    assert "watcherobot app run" in guide


def test_migration_inventory_is_clearly_historical() -> None:
    inventory = (
        ROOT / "docs" / "migration" / "server-daemon-dependencies.md"
    ).read_text(encoding="utf-8")

    assert "历史快照" in inventory
    assert "迁移结果" in inventory
    assert "当前只有两个" not in inventory
    assert "迁入完整 Daemon 后" not in inventory


def test_package_metadata_describes_managed_application_runtime() -> None:
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert (
        'description = "Python SDK for managed WatcheRobot Applications '
        'with a bundled Runtime/Daemon"'
    ) in metadata
    assert 'hardware = ["pyserial' not in metadata
