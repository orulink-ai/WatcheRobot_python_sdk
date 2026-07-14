from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_quickstart_is_minimal_and_independent() -> None:
    source = (ROOT / "examples" / "quickstart.py").read_text(encoding="utf-8")

    assert "WatcheRobot.connect" in source
    assert 'behavior.play("happy"' in source
    assert "hardware_smoke" not in source
    assert "camera.capture" not in source
    assert "microphone.open" not in source
    assert "motion.move_to" not in source


def test_capability_examples_are_separate_from_the_quickstart() -> None:
    expected = {
        "play_audio_file.py",
        "capture_photo.py",
        "record_microphone.py",
    }

    assert expected <= {path.name for path in (ROOT / "examples").glob("*.py")}
    assert (ROOT / "tools" / "hardware_smoke.py").is_file()


def test_runtime_artifacts_are_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "/artifacts/" in ignore
    assert "/.vscode/" in ignore
