import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi"


def load():
    spec = importlib.util.spec_from_file_location("pi_speech_preferences", ROOT / "speech_preferences.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_preference_round_trip_outside_project(tmp_path):
    module = load()
    path = tmp_path / "profile" / "speech-settings.json"
    assert module.load_backend(path) == "joyinside"
    module.save_backend(path, "doubao")
    assert module.load_backend(path) == "doubao"
    assert "access_token" not in path.read_text()
    with pytest.raises(ValueError):
        module.save_backend(path, "unsupported")
    assert module.load_backend(path) == "doubao"


@pytest.mark.parametrize("data", ['{"speech_backend":"unsupported"}', "[]", "not-json"])
def test_invalid_saved_backend_does_not_break_application(tmp_path, data):
    module = load()
    path = tmp_path / "settings.json"
    path.write_text(data)
    assert module.load_backend(path) == "joyinside"
