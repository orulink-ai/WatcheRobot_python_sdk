import json
from types import SimpleNamespace

import pytest

from watcherobot import cli


@pytest.mark.parametrize("disconnected", [True, False])
def test_usb_disconnect_cli_uses_only_usb_endpoint(monkeypatch, capsys, disconnected):
    state = SimpleNamespace(control_url="http://test")
    monkeypatch.setattr(cli, "ensure_runtime", lambda: (state, True))

    def request(base, endpoint, *, method, payload):
        assert base == "http://test"
        assert endpoint == "/daemon/devices/usb/disconnect"
        assert method == "POST" and payload == {}
        return {"disconnected": disconnected, "device": {"online": False}}

    monkeypatch.setattr(cli, "_request_json", request)
    assert cli.main(["robot", "usb", "disconnect"]) == 0
    assert json.loads(capsys.readouterr().out)["disconnected"] is disconnected
