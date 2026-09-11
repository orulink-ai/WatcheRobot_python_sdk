from __future__ import annotations

import argparse
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from watcherobot.cli import build_parser
from watcherobot.robot_cli import RobotCliError, _screen_duration_ms, _stable_work_id, run


@pytest.mark.parametrize(
    "arguments,attributes",
    [
        (["robot", "camera", "capture"], {"camera_command": "capture"}),
        (["robot", "camera", "record", "--with-audio"], {"with_audio": True, "fps": 5}),
        (["robot", "audio", "record", "--duration", "2"], {"duration": 2.0}),
        (["robot", "light", "set", "--zone", "head", "--color", "#123456"], {"zone": "head"}),
        (["robot", "screen", "play-work", "demo", "--clip", "main"], {"clip": "main"}),
        (["robot", "recording", "download", "rec_1", "--jsonl"], {"recording_id": "rec_1", "jsonl": True}),
    ],
)
def test_hardware_command_tree(arguments: list[str], attributes: dict[str, object]) -> None:
    parsed = build_parser().parse_args(arguments)
    for name, value in attributes.items():
        assert getattr(parsed, name) == value


def test_hardware_cli_rejects_active_application_before_opening_device_session() -> None:
    args = build_parser().parse_args(["robot", "capabilities"])
    def request(_base: str, path: str, **_kwargs):
        assert path == "/daemon/status"
        return {"application": {"state": "running"}}

    with pytest.raises(RobotCliError, match="app stop"):
        run(args, runtime_state=SimpleNamespace(control_url="http://x", external_url="ws://x"), request_json=request)


def test_stable_work_id_is_legal_and_content_addressed() -> None:
    first = _stable_work_id(Path("我的 动画.GIF"), b"one")
    second = _stable_work_id(Path("我的 动画.GIF"), b"two")
    assert first.startswith("work_")
    assert len(first) <= 23
    assert first != second


def test_screen_duration_preserves_gif_frame_timing() -> None:
    payload = io.BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
    frames[0].save(payload, format="GIF", save_all=True, append_images=frames[1:], duration=[120, 230])

    assert _screen_duration_ms(payload.getvalue()) == 350


def test_streaming_output_modes_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["robot", "camera", "record", "--json", "--jsonl"])
