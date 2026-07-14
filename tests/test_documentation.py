from pathlib import Path


ROOT = Path(__file__).parents[1]
PUBLIC_METHODS = (
    "WatcheRobot.connect",
    "robot.close",
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
    "robot.camera.capture",
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
