import base64
import io
from concurrent.futures import Future

import pytest
from PIL import Image

from watcherobot.errors import WatcheRobotError
from watcherobot.robot import WatcheRobot
from watcherobot.runtime.daemon.maintenance.works import (
    build_portable_work_package,
    normalize_work_document,
)


class ContractTransport:
    def __init__(self, capabilities: tuple[str, ...]) -> None:
        self.capabilities = capabilities
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.device_info = {"firmware_version": "v0.3.3"}

    def set_callbacks(self, *_callbacks) -> None:
        pass

    def send_command(self, message_type, data, timeout=None):
        self.commands.append((message_type, data))
        return {"type": "sys.ack", "code": 0, "data": {}}

    def send_command_nowait(self, message_type, data):
        self.commands.append((message_type, data))
        future = Future()
        future.set_result({"type": "sys.ack", "code": 0, "data": {}})
        return future

    def close(self) -> None:
        pass


def _animated_gif_data_url() -> str:
    output = io.BytesIO()
    frames = [
        Image.new("RGBA", (12, 8), (255, 0, 0, 255)),
        Image.new("RGBA", (12, 8), (0, 255, 0, 255)),
    ]
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=[80, 120],
        loop=0,
    )
    payload = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/gif;base64,{payload}"


def test_v033_contract_closes_official_work_clip_and_whole_work_playback() -> None:
    package = build_portable_work_package(
        {
            "workId": "mixed_faces",
            "revision": 1,
            "name": "官方与自制表情混合作品",
            "clips": [
                {
                    "id": "official-happy",
                    "kind": "expression",
                    "resourceId": "happy",
                    "label": "官方开心",
                    "startMs": 0,
                    "durationMs": 800,
                },
                {
                    "id": "work-custom-face",
                    "kind": "expression",
                    "resourceId": "custom-expression-1",
                    "label": "作品表情",
                    "startMs": 800,
                    "durationMs": 900,
                },
            ],
            "assets": [
                {
                    "id": "custom-expression-1",
                    "kind": "expression",
                    "name": "作品表情",
                    "fileName": "custom-face.gif",
                    "mimeType": "image/gif",
                    "dataUrl": _animated_gif_data_url(),
                }
            ],
        }
    )

    official_track, work_track = package.work["tracks"]
    assert official_track["clip_id"] == "official-happy"
    assert official_track["asset"] == {
        "source": "official",
        "resource_id": "happy",
        "kind": "anim",
    }
    assert work_track["clip_id"] == "work-custom-face"
    assert work_track["asset"]["source"] == "work"
    assert work_track["asset"]["resource_id"] != "custom-expression-1"

    transport = ContractTransport(
        ("resource.expression.official", "resource.work.expression.play")
    )
    robot = WatcheRobot._from_transport(transport)
    robot.expressions.play_official("happy")
    robot.works.play_expression("mixed_faces", clip_id=work_track["clip_id"])
    robot.works.play("mixed_faces")

    assert transport.commands == [
        (
            "resource.expression.play",
            {"source": "official", "resource_id": "happy"},
        ),
        (
            "resource.expression.play",
            {
                "source": "work",
                "work_id": "mixed_faces",
                "clip_id": "work-custom-face",
            },
        ),
        ("resource.work.play", {"work_id": "mixed_faces"}),
    ]


def test_new_expression_calls_fail_safely_on_old_firmware_without_breaking_work_play() -> None:
    transport = ContractTransport(())
    robot = WatcheRobot._from_transport(transport)

    with pytest.raises(WatcheRobotError, match="resource.expression.official"):
        robot.expressions.play_official("happy")
    with pytest.raises(WatcheRobotError, match="resource.work.expression.play"):
        robot.works.play_expression("mixed_faces", clip_id="work-custom-face")

    robot.works.play("mixed_faces")
    assert transport.commands == [("resource.work.play", {"work_id": "mixed_faces"})]


def test_old_v1_work_without_clip_id_remains_readable() -> None:
    legacy = {
        "schema_version": 1,
        "work_id": "legacy_faces",
        "name": "旧格式作品",
        "duration_ms": 800,
        "tracks": [
            {
                "type": "animation",
                "start_ms": 0,
                "duration_ms": 800,
                "asset": {"resource_id": "happy"},
            }
        ],
    }

    normalized = normalize_work_document(
        legacy,
        expected_id="legacy_faces",
        source="reader",
    )

    composition = normalized["composition"]
    assert composition["schema_version"] == 1
    assert composition["tracks"][0]["asset"]["resource_id"] == "happy"
    assert "clip_id" not in composition["tracks"][0]
