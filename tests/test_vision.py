from __future__ import annotations

from concurrent.futures import Future

import pytest

from watcherobot import VisionStatus, WatcheRobotError
from watcherobot.robot import WatcheRobot


class FakeTransport:
    def __init__(self, response: dict[str, object], *, capable: bool = True) -> None:
        self.capabilities = (
            ("vision.status.v1", "vision.model.select.v1", "face_tracking.control.v1")
            if capable
            else ()
        )
        self.device_info = {"device_id": "watcher-vision-test"}
        self.response = response
        self.commands: list[tuple[str, dict[str, object], float | None]] = []

    def set_callbacks(self, message_callback, binary_callback, disconnect_callback) -> None:
        self.message_callback = message_callback
        self.binary_callback = binary_callback
        self.disconnect_callback = disconnect_callback

    def send_command(self, message_type, data, timeout=None):
        self.commands.append((message_type, dict(data), timeout))
        return self.response

    def send_command_nowait(self, message_type, data):
        future: Future[dict[str, object]] = Future()
        future.set_result(self.response)
        return future

    def close(self) -> None:
        pass


def vision_response(*, model_available: bool = True) -> dict[str, object]:
    model: dict[str, object] = {"available": model_available}
    if model_available:
        model.update(
            {
                "model_id": 7,
                "model_name": "face-detector",
                "task": "detection",
                "contains_face_class": True,
            }
        )
    return {
        "type": "sys.ack",
        "code": 0,
        "data": {
            "type": "ctrl.vision.status.get",
            "backend": "sscma",
            "health": "ready",
            "status_code": 0,
            "initialized": True,
            "connected": True,
            "streaming": False,
            "inferencing": False,
            "capabilities": {
                "capture": True,
                "preview": True,
                "inference": True,
                "model_info": True,
                "model_management": False,
            },
            "model": model,
        },
    }


def test_vision_status_returns_typed_backend_health_and_model() -> None:
    transport = FakeTransport(vision_response())
    robot = WatcheRobot._from_transport(transport)

    status = robot.vision.status(timeout=2.5)

    assert isinstance(status, VisionStatus)
    assert status.backend == "sscma"
    assert status.health == "ready"
    assert status.connected
    assert status.capabilities.inference
    assert not status.capabilities.model_management
    assert status.model is not None
    assert status.model.model_id == 7
    assert status.model.name == "face-detector"
    assert status.model.contains_face_class
    assert transport.commands == [("ctrl.vision.status.get", {}, 2.5)]


def test_vision_convenience_queries_use_the_status_contract() -> None:
    robot = WatcheRobot._from_transport(FakeTransport(vision_response()))

    assert robot.vision.health().health == "ready"
    assert robot.vision.active_model() is not None
    assert robot.vision.capabilities().capture


def test_vision_model_selection_uses_a_distinct_capability_and_command() -> None:
    transport = FakeTransport(vision_response())
    robot = WatcheRobot._from_transport(transport)

    selected = robot.vision.select_model(4, timeout=1.5)

    assert selected is not None
    assert transport.commands == [
        ("ctrl.vision.model.select", {"model_id": 4}, 1.5),
        ("ctrl.vision.status.get", {}, 1.5),
    ]


def test_face_tracking_control_is_independent_from_preview() -> None:
    transport = FakeTransport(vision_response())
    robot = WatcheRobot._from_transport(transport)

    robot.face_tracking.start(timeout=1.0)
    robot.face_tracking.stop(policy="recenter", timeout=2.0)

    assert transport.commands == [
        ("ctrl.face_tracking.start", {}, 1.0),
        ("ctrl.face_tracking.stop", {"policy": "recenter"}, 2.0),
    ]


@pytest.mark.parametrize("model_id", [0, -1, 256, True])
def test_vision_model_selection_rejects_invalid_ids(model_id: object) -> None:
    robot = WatcheRobot._from_transport(FakeTransport(vision_response()))

    with pytest.raises(ValueError, match="model_id"):
        robot.vision.select_model(model_id)  # type: ignore[arg-type]


def test_vision_status_supports_backends_without_model_introspection() -> None:
    response = vision_response(model_available=False)
    data = response["data"]
    assert isinstance(data, dict)
    data["backend"] = "ptl"
    capabilities = data["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["inference"] = False
    capabilities["model_info"] = False
    robot = WatcheRobot._from_transport(FakeTransport(response))

    status = robot.vision.status()

    assert status.backend == "ptl"
    assert status.model is None
    assert not status.capabilities.inference


def test_vision_status_requires_firmware_capability() -> None:
    robot = WatcheRobot._from_transport(FakeTransport(vision_response(), capable=False))

    with pytest.raises(WatcheRobotError, match="vision.status.v1"):
        robot.vision.status()


@pytest.mark.parametrize(
    "field,value",
    [
        ("health", "mysterious"),
        ("initialized", 1),
        ("connected", 1),
        ("status_code", True),
    ],
)
def test_vision_status_rejects_malformed_device_payload(field: str, value: object) -> None:
    response = vision_response()
    data = response["data"]
    assert isinstance(data, dict)
    data[field] = value
    robot = WatcheRobot._from_transport(FakeTransport(response))

    with pytest.raises(WatcheRobotError, match="vision status ACK"):
        robot.vision.status()
