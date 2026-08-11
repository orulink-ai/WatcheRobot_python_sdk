import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
EXAMPLE_IDS = {
    "hello_robot": "example.hello_robot",
    "quickstart": "example.quickstart",
    "play_audio_file": "example.play_audio_file",
    "capture_photo": "example.capture_photo",
    "record_microphone": "example.record_microphone",
    "sdk_media_lab": "example.sdk_media_lab",
}


def test_every_example_is_a_complete_managed_application() -> None:
    for directory_name, app_id in EXAMPLE_IDS.items():
        root = ROOT / "examples" / directory_name
        manifest = json.loads(root.joinpath("app.json").read_text())
        source = root.joinpath("app.py").read_text(encoding="utf-8")

        assert manifest["id"] == app_id
        assert manifest["requires_watcherobot"]
        assert "ApplicationContext.from_environment()" in source
        assert "WatcheRobot.connect" not in source
        assert "WATCHEROBOT_PAIRING_CODE" not in source


def test_quickstart_demonstrates_domain_apis_through_context_robot() -> None:
    source = (
        ROOT / "examples" / "quickstart" / "app.py"
    ).read_text(encoding="utf-8")

    assert "app.robot.behavior.play" in source
    assert "app.robot.lights.set_color" in source
    assert "app.robot.motion.move_to" in source


def test_microphone_example_records_decoded_pcm() -> None:
    source = (
        ROOT / "examples" / "record_microphone" / "app.py"
    ).read_text(encoding="utf-8")

    assert "app.robot.microphone.record_pcm" in source
    assert "app.robot.microphone.record," not in source


def test_media_lab_is_a_local_managed_web_application() -> None:
    root = ROOT / "examples" / "sdk_media_lab"
    entrypoint = root.joinpath("app.py").read_text(encoding="utf-8")
    page = root.joinpath("web", "index.html").read_text(encoding="utf-8")
    script = root.joinpath("web", "app.js").read_text(encoding="utf-8")
    styles = root.joinpath("web", "styles.css").read_text(encoding="utf-8")

    assert "127.0.0.1" in entrypoint
    assert "ApplicationContext.from_environment()" in entrypoint
    assert "app.robot" in entrypoint
    assert "SDK 媒体实验室" in page
    assert "api/status" in script
    assert "api/actions/play-audio" in script
    assert "api/actions/capture-photo" in script
    assert "api/actions/record-microphone" in script
    assert "ws://" not in script
    assert "wss://" not in script
    assert "drawEmptyWaveform();" in script
    assert 'drawWaveform("/artifacts/microphone.wav")' not in script
    assert "[hidden]" in styles
    assert "display: none !important" in styles

    examples_guide = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
    hardware_guide = (ROOT / "docs" / "hardware-testing.md").read_text(encoding="utf-8")
    assert "sdk_media_lab" in examples_guide
    assert "sdk_media_lab" in hardware_guide
    assert "127.0.0.1" in hardware_guide


def test_runtime_artifacts_are_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/artifacts/" in ignore
    assert "/examples/*/artifacts/" in ignore
    assert "/.vscode/" in ignore

    result = subprocess.run(
        ["git", "check-ignore", "examples/capture_photo/artifacts/camera.jpg"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
