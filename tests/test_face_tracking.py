from __future__ import annotations

import asyncio
import json
import struct
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from watcherobot import FaceBox, FaceTrackingFrame, WatcheRobotError
from watcherobot.protocol import FRAME_VIDEO, BinaryFrame
from watcherobot.robot import WatcheRobot


class FakeTransport:
    def __init__(self, *, capable: bool = True) -> None:
        self.capabilities = (
            ("face_tracking.preview.v1", "face_tracking.control.v1")
            if capable
            else ()
        )
        self.device_info = {"device_id": "watcher-face-preview-test"}
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.message_callback = None
        self.binary_callback = None
        self.disconnect_callback = None
        self.closed = False

    def set_callbacks(self, message_callback, binary_callback, disconnect_callback) -> None:
        self.message_callback = message_callback
        self.binary_callback = binary_callback
        self.disconnect_callback = disconnect_callback

    def send_command(self, message_type, data, timeout=None):
        self.commands.append((message_type, dict(data)))
        return {"type": "sys.ack", "code": 0, "data": {}}

    def send_command_nowait(self, message_type, data):
        self.commands.append((message_type, dict(data)))
        future: Future[dict[str, object]] = Future()
        future.set_result({"type": "sys.ack", "code": 0, "data": {}})
        return future

    def close(self) -> None:
        self.closed = True


def telemetry(sequence: int, *, error_x: float = 4.0) -> dict[str, object]:
    return {
        "v": 1,
        "kind": "frame",
        "seq": sequence,
        "t": 1000 + sequence,
        "age": 2,
        "size": [416, 416],
        "boxes": [[100, 110, 80, 90, 87, 0]],
        "perf": [1, 33, 2],
        "error": [error_x, -3.0],
        "velocity": [12.0, -4.0],
        "visible": True,
        "state": 2,
        "command": 1,
    }


def image_packet(sequence: int, *, jpeg: bytes | None = None) -> bytes:
    payload = jpeg or b"\xff\xd8face-preview\xff\xd9"
    return struct.pack(
        "<4sBBHIIHHI",
        b"FTW1",
        1,
        1,
        24,
        sequence,
        1000 + sequence,
        416,
        416,
        len(payload),
    ) + payload


def emit_pair(transport: FakeTransport, sequence: int) -> None:
    assert transport.message_callback is not None
    assert transport.binary_callback is not None
    transport.message_callback(
        {
            "type": "evt.face_tracking.preview.frame",
            "code": 0,
            "data": telemetry(sequence),
        }
    )
    transport.binary_callback(
        BinaryFrame(FRAME_VIDEO, 0, 0, sequence, image_packet(sequence))
    )


def test_public_preview_pairs_typed_telemetry_and_jpeg() -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)

    preview = robot.face_tracking.open_preview(
        width=416,
        height=416,
        frame_stride=1,
    )
    emit_pair(transport, 7)
    frame = preview.read(timeout=0)

    assert isinstance(frame, FaceTrackingFrame)
    assert frame.sequence == 7
    assert frame.width == 416
    assert frame.height == 416
    assert frame.jpeg.startswith(b"\xff\xd8")
    assert frame.faces == (
        FaceBox(x=100, y=110, width=80, height=90, score=87, target=0),
    )
    assert frame.telemetry.error_x_percent == 4.0
    assert frame.telemetry.inference_ms == 33.0
    assert transport.commands[0] == (
        "ctrl.face_tracking.preview.start",
        {"frame_stride": 1, "width": 416, "height": 416},
    )

    preview.close()
    preview.close()
    assert transport.commands[-1] == (
        "ctrl.face_tracking.preview.stop",
        {"policy": "hold"},
    )
    assert [name for name, _data in transport.commands].count(
        "ctrl.face_tracking.preview.stop"
    ) == 1


def test_preview_keeps_latest_complete_frame_for_slow_consumers() -> None:
    transport = FakeTransport()
    preview = WatcheRobot._from_transport(transport).face_tracking.open_preview()

    emit_pair(transport, 1)
    emit_pair(transport, 2)

    assert preview.read(timeout=0).sequence == 2
    assert preview.dropped_frames == 1
    with pytest.raises(TimeoutError, match="preview frame"):
        preview.read(timeout=0)
    preview.close()


def test_preview_pairs_out_of_order_parts_and_ignores_malformed_packets() -> None:
    transport = FakeTransport()
    preview = WatcheRobot._from_transport(transport).face_tracking.open_preview()
    assert transport.message_callback is not None
    assert transport.binary_callback is not None

    transport.binary_callback(BinaryFrame(FRAME_VIDEO, 0, 0, 3, b"bad"))
    transport.binary_callback(
        BinaryFrame(FRAME_VIDEO, 0, 0, 4, image_packet(4))
    )
    transport.message_callback(
        {
            "type": "evt.face_tracking.preview.frame",
            "code": 0,
            "data": telemetry(4),
        }
    )

    assert preview.read(timeout=0).sequence == 4
    preview.close()


def test_preview_supports_async_iteration_and_recenter_on_exit() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        preview = WatcheRobot._from_transport(transport).face_tracking.open_preview(
            stop_policy="recenter"
        )
        async with preview:
            pending = asyncio.create_task(anext(preview))
            await asyncio.sleep(0)
            emit_pair(transport, 11)
            assert (await asyncio.wait_for(pending, timeout=1)).sequence == 11

        assert transport.commands[-1] == (
            "ctrl.face_tracking.preview.stop",
            {"policy": "recenter"},
        )

    asyncio.run(scenario())


def test_preview_rejects_invalid_contract_and_concurrent_session() -> None:
    robot = WatcheRobot._from_transport(FakeTransport())
    preview = robot.face_tracking.open_preview()
    with pytest.raises(WatcheRobotError, match="already open"):
        robot.face_tracking.open_preview()
    preview.close()

    for options in (
        {"width": 320, "height": 240},
        {"frame_stride": 0},
        {"frame_stride": True},
        {"queue_size": 0},
        {"stop_policy": "scan"},
    ):
        with pytest.raises(ValueError):
            robot.face_tracking.open_preview(**options)

    incapable = WatcheRobot._from_transport(FakeTransport(capable=False))
    with pytest.raises(WatcheRobotError, match="face_tracking.preview.v1"):
        incapable.face_tracking.open_preview()


@pytest.mark.parametrize("capabilities", [
    ("face_tracking.preview.v1", "face_tracking.control.v1"),
    ("face_tracking.preview.v1",),
])
def test_domain_stop_closes_an_active_preview_before_reopening(
    capabilities: tuple[str, ...], monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()
    transport.capabilities = capabilities
    robot = WatcheRobot._from_transport(transport)
    preview = robot.face_tracking.open_preview()

    send_command = transport.send_command
    timeouts: list[float | None] = []

    def record_timeout(message_type, data, timeout=None):
        timeouts.append(timeout)
        return send_command(message_type, data, timeout)

    monkeypatch.setattr(transport, "send_command", record_timeout)

    robot.face_tracking.stop(policy="hold", timeout=0.75)

    assert timeouts == [0.75]
    assert transport.commands[-1] == (
        "ctrl.face_tracking.preview.stop",
        {"policy": "hold"},
    )
    with pytest.raises(WatcheRobotError, match="preview closed"):
        preview.read(timeout=0)
    reopened = robot.face_tracking.open_preview()
    reopened.close()


def test_disconnect_wakes_blocked_preview_reader() -> None:
    transport = FakeTransport()
    preview = WatcheRobot._from_transport(transport).face_tracking.open_preview()
    errors: list[BaseException] = []

    def read() -> None:
        try:
            preview.read()
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    reader = threading.Thread(target=read)
    reader.start()
    assert transport.disconnect_callback is not None
    transport.disconnect_callback()
    reader.join(1)

    assert not reader.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], WatcheRobotError)
    assert "disconnected" in str(errors[0])


def test_robot_close_stops_owned_tracking_before_closing_transport() -> None:
    transport = FakeTransport()
    with WatcheRobot._from_transport(transport) as robot:
        robot.face_tracking.start()
    robot.close()
    assert transport.commands == [
        ("ctrl.face_tracking.start", {}),
        ("ctrl.face_tracking.stop", {"policy": "hold"}),
    ]
    assert transport.closed


def test_robot_close_does_not_stop_unowned_or_already_stopped_tracking() -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    robot.close()
    assert transport.commands == []
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    robot.face_tracking.start()
    robot.face_tracking.stop()
    robot.close()
    assert transport.commands == [
        ("ctrl.face_tracking.start", {}),
        ("ctrl.face_tracking.stop", {"policy": "hold"}),
    ]


def test_robot_close_releases_transport_when_tracking_stop_fails(monkeypatch, caplog) -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    robot.face_tracking.start()
    attempted = []

    def fail_stop(message_type, data, timeout=None):
        assert not transport.closed
        attempted.append((message_type, timeout))
        raise TimeoutError("device unavailable")

    monkeypatch.setattr(transport, "send_command", fail_stop)
    robot.close()
    assert attempted == [("ctrl.face_tracking.stop", 2.0)]
    assert transport.closed
    assert "Face tracking cleanup failed while closing robot" in caplog.text


def test_concurrent_start_finishes_before_close_stops_tracking(monkeypatch) -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    started, release_start, closing = threading.Event(), threading.Event(), threading.Event()
    send = transport.send_command
    cleanup = robot.face_tracking._close

    def blocked_start(message_type, data, timeout=None):
        assert not transport.closed
        if message_type == "ctrl.face_tracking.start":
            started.set()
            assert release_start.wait(2)
        return send(message_type, data, timeout=timeout)

    def observed_cleanup():
        closing.set()
        cleanup()

    monkeypatch.setattr(transport, "send_command", blocked_start)
    monkeypatch.setattr(robot.face_tracking, "_close", observed_cleanup)
    with ThreadPoolExecutor(max_workers=2) as executor:
        start = executor.submit(robot.face_tracking.start)
        try:
            assert started.wait(2)
            close = executor.submit(robot.close)
            assert closing.wait(2)
            assert not close.done()
        finally:
            release_start.set()
        start.result(timeout=2)
        close.result(timeout=2)
    assert transport.commands == [
        ("ctrl.face_tracking.start", {}),
        ("ctrl.face_tracking.stop", {"policy": "hold"}),
    ]
    assert transport.closed


def test_start_is_rejected_after_close_begins(monkeypatch) -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    closing, release_close = threading.Event(), threading.Event()
    cleanup = robot.face_tracking._close

    def blocked_cleanup():
        closing.set()
        assert release_close.wait(2)
        cleanup()

    monkeypatch.setattr(robot.face_tracking, "_close", blocked_cleanup)
    with ThreadPoolExecutor(max_workers=1) as executor:
        close = executor.submit(robot.close)
        try:
            assert closing.wait(2)
            with pytest.raises(WatcheRobotError, match="closed"):
                robot.face_tracking.start()
            assert not transport.closed
        finally:
            release_close.set()
        close.result(timeout=2)
    assert transport.commands == []
    assert transport.closed


def test_failed_start_does_not_claim_tracking_ownership(monkeypatch) -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    attempted = []

    def fail_start(message_type, data, timeout=None):
        attempted.append(message_type)
        raise TimeoutError("start acknowledgement missing")

    monkeypatch.setattr(transport, "send_command", fail_start)
    with pytest.raises(TimeoutError):
        robot.face_tracking.start()
    robot.close()
    assert attempted == ["ctrl.face_tracking.start"]
    assert transport.closed


def test_camera_preview_stop_releases_shared_tracking_on_robot_close() -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    robot.face_tracking.start()
    preview = robot.face_tracking.open_preview()
    robot.close()
    robot.close()
    assert [command for command, _ in transport.commands] == [
        "ctrl.face_tracking.start",
        "ctrl.face_tracking.preview.start",
        "ctrl.face_tracking.preview.stop",
    ]
    assert transport.commands[-1][1] == {"policy": "hold"}
    assert preview.closed and transport.closed


def test_close_interrupts_a_start_that_does_not_return(monkeypatch, caplog) -> None:
    from watcherobot import vision

    monkeypatch.setattr(vision, "_CLOSE_CONTROL_WAIT_SECONDS", 0.02, raising=False)
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    started, closed = threading.Event(), threading.Event()

    def blocked_start(message_type, data, timeout=None):
        started.set()
        if not closed.wait(1):
            raise TimeoutError("start was not interrupted by transport closure")
        raise WatcheRobotError("transport closed")

    def close_transport():
        transport.closed = True
        closed.set()

    monkeypatch.setattr(transport, "send_command", blocked_start)
    monkeypatch.setattr(transport, "close", close_transport)
    with ThreadPoolExecutor(max_workers=2) as executor:
        start = executor.submit(robot.face_tracking.start)
        assert started.wait(1)
        close = executor.submit(robot.close)
        close.result(timeout=0.5)
        with pytest.raises(WatcheRobotError, match="transport closed"):
            start.result(timeout=0.5)
    assert transport.closed
    assert "Face tracking cleanup failed while closing robot" in caplog.text
