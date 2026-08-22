from __future__ import annotations

from watcherobot import cli

from .test_vision import vision_response


def test_parser_exposes_the_minimal_vision_commands() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["robot", "vision", "status"]).vision_command == "status"
    assert parser.parse_args(["robot", "vision", "model", "list"]).model_command == "list"
    selected = parser.parse_args(["robot", "vision", "model", "use", "4"])
    assert selected.model_command == "use"
    assert selected.model_id == 4
    assert parser.parse_args(["robot", "face-track", "on"]).face_track_command == "on"
    assert parser.parse_args(["robot", "face-track", "off"]).face_track_command == "off"


def test_vision_cli_commands_map_to_device_business_frames(monkeypatch, capsys) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_command(message_type: str, data: dict[str, object]):
        calls.append((message_type, data))
        if message_type == "ctrl.vision.status.get":
            return vision_response()
        return {
            "type": "sys.ack",
            "code": 0,
            "data": {"type": message_type, "command_id": "test"},
        }

    monkeypatch.setattr(cli, "_run_robot_business_command", fake_command)

    assert cli.main(["robot", "vision", "status"]) == 0
    assert cli.main(["robot", "vision", "model", "list"]) == 0
    assert cli.main(["robot", "vision", "model", "use", "7"]) == 0
    assert cli.main(["robot", "face-track", "on"]) == 0
    assert cli.main(["robot", "face-track", "off"]) == 0

    assert calls == [
        ("ctrl.vision.status.get", {}),
        ("ctrl.vision.status.get", {}),
        ("ctrl.vision.model.select", {"model_id": 7}),
        ("ctrl.vision.status.get", {}),
        ("ctrl.face_tracking.start", {}),
        ("ctrl.face_tracking.stop", {"policy": "hold"}),
    ]
    output = capsys.readouterr().out
    assert "face-detector" in output
    assert "Face tracking started" in output
    assert "Face tracking stopped" in output
