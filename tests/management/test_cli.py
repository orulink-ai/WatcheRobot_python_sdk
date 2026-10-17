import io
import json

from watcherobot.management.cli import main


def test_cleanup_returns_the_same_status_contract_as_other_settings_actions(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    request = {
        "action": "cleanup",
        "desktop": {
            "manager": ">=1,<2",
            "management": ">=1,<2",
            "distribution": ">=1,<2",
        },
    }
    monkeypatch.setattr(
        "sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))
    )
    assert main() == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["ok"] is True
    assert "actions" in result["data"]
    assert result["data"]["operation"]["phase"] == "completed"
    assert result["data"]["cleanup"]["errors"] == []
