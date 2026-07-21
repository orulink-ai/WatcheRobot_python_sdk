import threading

import pytest

from watcherobot import (
    BackTouchEvent,
    RollerEvent,
    ScreenTouchEvent,
    WatcheRobot,
    WatcheRobotError,
)


class FakeTransport:
    def __init__(self) -> None:
        self.capabilities = (
            "input.back_touch",
            "input.screen_touch",
            "input.roller",
        )
        self.device_info = {"device_id": "watcher-input-test"}
        self.message_callback = None
        self.binary_callback = None
        self.disconnect_callback = None
        self.closed = False

    def set_callbacks(self, message_callback, binary_callback, disconnect_callback) -> None:
        self.message_callback = message_callback
        self.binary_callback = binary_callback
        self.disconnect_callback = disconnect_callback

    def close(self) -> None:
        self.closed = True


def emit(transport: FakeTransport, data: dict) -> None:
    assert transport.message_callback is not None
    transport.message_callback({"type": "evt.sdk.input", "code": 0, "data": data})


def test_input_events_are_typed_and_keep_wire_order() -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)

    emit(
        transport,
        {
            "source": "back_touch",
            "action": "press",
            "touch_id": 0,
            "timestamp_ms": 101,
        },
    )
    emit(
        transport,
        {
            "source": "screen_touch",
            "action": "tap",
            "x": 123,
            "y": 234,
            "timestamp_ms": 102,
        },
    )
    emit(
        transport,
        {
            "source": "roller",
            "action": "rotate",
            "delta": -1,
            "timestamp_ms": 103,
        },
    )

    assert robot.inputs.wait(timeout=0) == BackTouchEvent(
        action="press", touch_id=0, timestamp_ms=101
    )
    assert robot.inputs.wait(timeout=0) == ScreenTouchEvent(x=123, y=234, timestamp_ms=102)
    assert robot.inputs.wait(timeout=0) == RollerEvent(delta=-1, timestamp_ms=103)


@pytest.mark.parametrize(
    "data",
    [
        {"source": "back_touch", "action": "tap", "touch_id": 0, "timestamp_ms": 1},
        {"source": "back_touch", "action": "press", "touch_id": -1, "timestamp_ms": 1},
        {"source": "screen_touch", "action": "tap", "x": 1, "timestamp_ms": 1},
        {"source": "screen_touch", "action": "press", "x": 1, "y": 2, "timestamp_ms": 1},
        {"source": "roller", "action": "rotate", "delta": 0, "timestamp_ms": 1},
        {"source": "roller", "action": "rotate", "delta": True, "timestamp_ms": 1},
        {"source": "unknown", "action": "tap", "timestamp_ms": 1},
        {"source": "roller", "action": "rotate", "delta": 1, "timestamp_ms": -1},
    ],
)
def test_malformed_input_events_are_ignored(data: dict) -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)

    emit(transport, data)

    with pytest.raises(TimeoutError, match="input event"):
        robot.inputs.wait(timeout=0)


def test_input_queue_drops_oldest_event_without_blocking_transport() -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)

    for timestamp_ms in range(65):
        emit(
            transport,
            {
                "source": "roller",
                "action": "rotate",
                "delta": 1,
                "timestamp_ms": timestamp_ms,
            },
        )

    assert robot.inputs.dropped_events == 1
    assert robot.inputs.wait(timeout=0) == RollerEvent(delta=1, timestamp_ms=1)


def test_input_wait_validates_timeout_and_clear_discards_buffered_events() -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    emit(
        transport,
        {
            "source": "roller",
            "action": "rotate",
            "delta": 1,
            "timestamp_ms": 1,
        },
    )

    robot.inputs.clear()

    for invalid_timeout in (-1, float("inf"), float("nan"), True):
        with pytest.raises(ValueError, match="finite non-negative"):
            robot.inputs.wait(timeout=invalid_timeout)
    with pytest.raises(TimeoutError, match="input event"):
        robot.inputs.wait(timeout=0)


def test_disconnect_wakes_input_waiter() -> None:
    transport = FakeTransport()
    robot = WatcheRobot._from_transport(transport)
    errors: list[BaseException] = []

    def wait_for_input() -> None:
        try:
            robot.inputs.wait()
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    waiter = threading.Thread(target=wait_for_input)
    waiter.start()
    assert transport.disconnect_callback is not None
    transport.disconnect_callback()
    waiter.join(1)

    assert not waiter.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], WatcheRobotError)
    assert "disconnected" in str(errors[0])
