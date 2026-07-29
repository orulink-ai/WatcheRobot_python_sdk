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
