from pathlib import Path
import re


def test_quickstart_uses_behavior_installed_by_default_firmware() -> None:
    source = (Path(__file__).parents[1] / "examples" / "quickstart.py").read_text(encoding="utf-8")

    assert 'robot.behavior.play("happy")' in source
    assert 'robot.behavior.play("greeting")' not in source
    assert 'os.environ.get("WATCHEROBOT_PAIRING_CODE")' in source
    assert re.search(r'pairing_code="\d{6}"', source) is None
