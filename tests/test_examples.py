import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
EXAMPLE_IDS = {
    "hello_robot": "example.hello_robot",
    "quickstart": "example.quickstart",
    "play_audio_file": "example.play_audio_file",
    "capture_photo": "example.capture_photo",
    "record_microphone": "example.record_microphone",
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
    assert "app.robot.display.show_text" in source
    assert "app.robot.display.clear" in source
    assert "app.robot.lights.set_color" in source
    assert "app.robot.motion.move_to" in source


def test_runtime_artifacts_are_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/artifacts/" in ignore
    assert "/.vscode/" in ignore
