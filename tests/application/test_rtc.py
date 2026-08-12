from __future__ import annotations

import json
from concurrent.futures import Future

import pytest

from watcherobot.application.rtc import ApplicationRtc


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.listeners = []

    def add_message_listener(self, listener) -> None:
        self.listeners.append(listener)

    def remove_message_listener(self, listener) -> None:
        self.listeners.remove(listener)

    def send_device(self, frame: str | bytes) -> Future[None]:
        self.sent.append(frame)
        future: Future[None] = Future()
        future.set_result(None)
        return future

    def emit(self, message: dict[str, object]) -> None:
        for listener in tuple(self.listeners):
            listener(message)


def test_rtc_builds_exact_watcher_rtc_session_and_signal_envelopes() -> None:
    transport = FakeTransport()
    rtc = ApplicationRtc(
        transport,  # type: ignore[arg-type]
        id_factory=iter(("client-0001", "session-0001", "command-0001", "command-0002")).__next__,
    )

    session = rtc.start(mode="video")
    rtc.send_offer("v=0\r\n")

    assert session == {
        "active": True,
        "client_id": "client-0001",
        "session_id": "session-0001",
        "state": "starting",
        "mode": "video",
        "last_error": None,
        "capabilities": {},
        "stats": {},
    }
    assert [json.loads(frame) for frame in transport.sent] == [
        {
            "type": "ctrl.rtc.session.start",
            "protocol": "watcher-rtc/1",
            "client_id": "client-0001",
            "session_id": "session-0001",
            "command_id": "command-0001",
            "data": {"mode": "video"},
        },
        {
            "type": "ctrl.rtc.signal",
            "protocol": "watcher-rtc/1",
            "client_id": "client-0001",
            "session_id": "session-0001",
            "command_id": "command-0002",
            "data": {"kind": "offer", "sdp": "v=0\r\n"},
        },
    ]


def test_rtc_filters_other_sessions_and_retains_ordered_browser_events() -> None:
    transport = FakeTransport()
    rtc = ApplicationRtc(
        transport,  # type: ignore[arg-type]
        id_factory=iter(("client-0001", "session-0001", "command-0001")).__next__,
    )
    rtc.start()
    transport.emit(
        {
            "type": "evt.rtc.state",
            "protocol": "watcher-rtc/1",
            "client_id": "other-client",
            "session_id": "other-session",
            "data": {"state": "connected"},
        }
    )
    transport.emit(
        {
            "type": "evt.rtc.capabilities",
            "protocol": "watcher-rtc/1",
            "client_id": "client-0001",
            "session_id": "session-0001",
            "data": {"video": {"codec": "MJPEG", "width": 640, "height": 480}},
        }
    )
    transport.emit(
        {
            "type": "evt.rtc.state",
            "protocol": "watcher-rtc/1",
            "client_id": "client-0001",
            "session_id": "session-0001",
            "data": {"state": "connected"},
        }
    )

    assert rtc.snapshot()["state"] == "connected"
    assert rtc.snapshot()["capabilities"] == {
        "video": {"codec": "MJPEG", "width": 640, "height": 480}
    }
    assert [event["id"] for event in rtc.events()] == [1, 2]
    assert [event["message"]["type"] for event in rtc.events(after=1)] == [
        "evt.rtc.state"
    ]


def test_rtc_rejects_invalid_data_before_sending_to_device() -> None:
    transport = FakeTransport()
    rtc = ApplicationRtc(transport)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="mode"):
        rtc.start(mode="screen")
    rtc.start()
    with pytest.raises(ValueError, match="sdp"):
        rtc.send_offer("")
    with pytest.raises(ValueError, match="candidate"):
        rtc.send_candidate("", sdp_mid="0", sdp_mline_index=0)


def test_rtc_stop_is_idempotent_and_close_unregisters_listener() -> None:
    transport = FakeTransport()
    rtc = ApplicationRtc(transport)  # type: ignore[arg-type]
    rtc.start()

    assert rtc.stop() is True
    assert rtc.stop() is False
    rtc.close()

    assert transport.listeners == []
    assert json.loads(transport.sent[-1])["type"] == "ctrl.rtc.session.stop"


def test_rtc_stop_can_retry_after_transport_send_failure() -> None:
    transport = FakeTransport()
    rtc = ApplicationRtc(transport)  # type: ignore[arg-type]
    rtc.start()
    original_send = transport.send_device
    stop_attempts = 0

    def fail_first_stop(frame: str | bytes) -> Future[None]:
        nonlocal stop_attempts
        if json.loads(frame)["type"] == "ctrl.rtc.session.stop":
            stop_attempts += 1
            if stop_attempts == 1:
                future: Future[None] = Future()
                future.set_exception(ConnectionError("device channel unavailable"))
                return future
        return original_send(frame)

    transport.send_device = fail_first_stop  # type: ignore[method-assign]

    with pytest.raises(ConnectionError, match="device channel unavailable"):
        rtc.stop()

    assert rtc.snapshot()["active"] is True
    assert rtc.snapshot()["state"] == "starting"
    assert rtc.stop() is True
    assert stop_attempts == 2


def test_rtc_reset_abandons_offline_session_and_ignores_late_events() -> None:
    transport = FakeTransport()
    rtc = ApplicationRtc(
        transport,  # type: ignore[arg-type]
        id_factory=iter(("client-0001", "session-0001", "command-0001")).__next__,
    )
    rtc.start()
    transport.emit(
        {
            "type": "evt.rtc.stats",
            "protocol": "watcher-rtc/1",
            "client_id": "client-0001",
            "session_id": "session-0001",
            "data": {"source_frames": 3},
        }
    )

    assert rtc.reset(reason="device_offline") is True
    transport.emit(
        {
            "type": "evt.rtc.state",
            "protocol": "watcher-rtc/1",
            "client_id": "client-0001",
            "session_id": "session-0001",
            "data": {"state": "connected"},
        }
    )

    assert rtc.snapshot() == {
        "active": False,
        "client_id": None,
        "session_id": None,
        "state": "failed",
        "mode": None,
        "last_error": "device_offline",
        "capabilities": {},
        "stats": {},
    }
    assert rtc.events() == []
    assert rtc.reset(reason="device_offline") is False
