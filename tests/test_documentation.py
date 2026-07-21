from pathlib import Path

from watcherobot import __version__
from watcherobot.protocol import PROTOCOL_VERSION


ROOT = Path(__file__).parents[1]
PUBLIC_METHODS = (
    "WatcheRobot.connect",
    "robot.close",
    "robot.device_info",
    "robot.capabilities",
    "robot.supports",
    "robot.behavior.play",
    "robot.behavior.stop",
    "robot.animation.play",
    "robot.animation.stop",
    "robot.motion.move_to",
    "robot.motion.set_target",
    "robot.motion.play_action",
    "robot.motion.stop",
    "robot.audio.play",
    "robot.audio.play_file",
    "robot.audio.play_pcm",
    "robot.audio.stop",
    "robot.lights.set_color",
    "robot.lights.play_effect",
    "robot.lights.off",
    "robot.microphone.open",
    "robot.microphone.record",
    "MicrophoneSession.read",
    "MicrophoneSession.close",
    "robot.camera.capture",
    "robot.inputs.wait",
    "robot.inputs.clear",
    "Job.wait",
    "Job.cancel",
    "AudioRecording.save",
    "ImageFrame.save",
)


def test_readmes_contain_a_complete_supported_api_table() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")

        assert "|" in readme
        for method in PUBLIC_METHODS:
            assert f"`{method}" in readme, f"{name} does not document {method}"


def test_readmes_link_alpha_install_compatibility_resources_and_troubleshooting() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")

        assert "https://test.pypi.org/simple/" in readme
        assert f"watcherobot=={__version__}" in readme
        assert f"`{PROTOCOL_VERSION}`" in readme
        assert "`V3.1`" in readme
        assert "docs/resources.md" in readme
        assert "docs/troubleshooting.md" in readme
        assert "period_ms" in readme
        assert "3.10 / 3.11 / 3.12" in readme
        assert "https://github.com/orulink-ai/WatcheRobot_esp32/pull/96" in readme
        assert "gh repo clone orulink-ai/WatcheRobot_esp32" in readme
        assert "gh pr checkout 96" in readme
        assert "esp-idf-v5.2.1" in readme
        assert "flash-monitor.ps1 COMx -NoWake" in readme
        assert "https://test.pypi.org/project/watcherobot/" in readme
        assert "camera.capture" in readme
        assert "JobFailedError" in readme
        assert "error.reason" in readme


def test_readmes_provide_a_copyable_first_connection_without_repository_examples() -> None:
    for name in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")

        assert 'pairing_code = input(' in readme
        assert "WatcheRobot.connect(pairing_code=pairing_code)" in readme
        assert "Expected output" in readme or "正常输出" in readme
        assert "python examples/hello_robot.py" not in readme


def test_resource_guide_marks_verified_ids_and_catalog_limit() -> None:
    guide = (ROOT / "docs" / "resources.md").read_text(encoding="utf-8")

    for resource_id in ("happy", "smile", "blink", "breathing", "rainbow", "status_pulse"):
        assert f"`{resource_id}`" in guide
    assert "robot.capabilities" in guide
    assert "not_found" in guide
    assert "not exhaustive" in guide.lower()


def test_troubleshooting_guide_covers_common_failures() -> None:
    guide = (ROOT / "docs" / "troubleshooting.md").read_text(encoding="utf-8")

    for failure in (
        "ConnectionTimeoutError",
        "AuthenticationError",
        "protocol_version_mismatch",
        "not_found",
        "TimeoutError",
        "dropped_frames",
    ):
        assert failure in guide
    assert "error.job_id" in guide
    assert "WatcheRobot.connect" in guide


def test_release_guide_uses_the_current_package_version() -> None:
    guide = (ROOT / "docs" / "releasing.md").read_text(encoding="utf-8")

    assert f"watcherobot=={__version__}" in guide
    assert f"v{__version__}" in guide
