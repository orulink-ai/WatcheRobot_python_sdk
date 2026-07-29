from pathlib import Path

from watcherobot import __version__


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
        assert ".wapp" in readme
        assert "WatcheRobot.connect" not in readme
        assert "BackgroundTransport" not in readme
        assert "SDK_DISCOVER" not in readme


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


def test_release_guide_uses_the_current_package_version() -> None:
    guide = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    assert f"watcherobot=={__version__}" in guide
    assert f"v{__version__}" in guide


def test_release_guide_uses_managed_application_acceptance() -> None:
    guide = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    assert "tools/hardware_smoke.py" not in guide
    assert "tests/fakes/" not in guide
    assert "--no-deps" not in guide
    assert "--extra-index-url https://pypi.org/simple/" in guide
    assert "watcherobot app run" in guide


def test_readmes_distinguish_current_and_target_desktop_support() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    normalized_english = " ".join(english.split())
    normalized_chinese = "".join(chinese.split())

    assert "Desktop integration is still pending" in normalized_english
    assert "桌面端接入仍待完成" in normalized_chinese
    assert "/daemon/devices/pair" in english
    assert "/daemon/devices/pair" in chinese


def test_hardware_guide_contains_standalone_pairing_step() -> None:
    guide = (ROOT / "docs" / "hardware-testing.md").read_text(
        encoding="utf-8"
    )

    assert "control_url" in guide
    assert "/daemon/devices/pair" in guide
    assert '"target_mode":"desktop_link"' in guide
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
