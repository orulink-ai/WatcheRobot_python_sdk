from __future__ import annotations

import json
from concurrent.futures import Future
from threading import Timer

import pytest

from watcherobot.application.rtc import (
    ApplicationRtc,
    RTC_AUDIO_CAPABILITY,
    RTC_VIDEO_CAPABILITY,
    RtcSessionRejectedError,
)


def test_rtc_capability_names_are_public_and_feature_specific() -> None:
    assert RTC_AUDIO_CAPABILITY == "rtc.audio.full_duplex.v1"
    assert RTC_VIDEO_CAPABILITY == "rtc.video.mjpeg.v1"


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
        message = json.loads(frame)
        if message["type"] in {"ctrl.rtc.session.start", "ctrl.rtc.session.stop"}:
            self.emit(
                {
                    "type": "sys.ack",
                    "code": 0,
                    "protocol": message["protocol"],
                    "client_id": message["client_id"],
                    "session_id": message["session_id"],
                    "command_id": message["command_id"],
                    "data": {"type": message["type"]},
                }
            )
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
    assert [event["id"] for event in rtc.events()] == [1, 2, 3]
    assert [event["message"]["type"] for event in rtc.events(after=2)] == [
        "evt.rtc.state"
    ]


def test_rtc_start_waits_for_device_nack_and_reports_busy_owner() -> None:
    class RejectingTransport(FakeTransport):
        def send_device(self, frame: str | bytes) -> Future[None]:
            self.sent.append(frame)
            message = json.loads(frame)
            future: Future[None] = Future()
            future.set_result(None)
            if message["type"] == "ctrl.rtc.session.start":
                Timer(
                    0.02,
                    lambda: self.emit(
                        {
                            "type": "sys.nack",
                            "code": 1,
                            "protocol": message["protocol"],
                            "client_id": message["client_id"],
                            "session_id": message["session_id"],
                            "command_id": message["command_id"],
                            "data": {
                                "type": message["type"],
                                "error": "busy",
                                "owner": "audio_playback",
                            },
                        }
                    ),
                ).start()
            return future

    transport = RejectingTransport()
    rtc = ApplicationRtc(transport, send_timeout=0.5)  # type: ignore[arg-type]

    with pytest.raises(RtcSessionRejectedError) as rejected:
        rtc.start(mode="video")

    assert rejected.value.error == "busy"
    assert rejected.value.owner == "audio_playback"
    assert rtc.snapshot()["active"] is False
    assert rtc.snapshot()["last_error"] == "busy"


def test_rtc_keeps_first_terminal_error_when_late_signals_are_rejected() -> None:
    transport = FakeTransport()
    rtc = ApplicationRtc(transport)  # type: ignore[arg-type]
    session = rtc.start()
    transport.emit(
        {
            "type": "sys.nack",
            "code": 1,
            "protocol": "watcher-rtc/1",
            "client_id": session["client_id"],
            "session_id": session["session_id"],
            "command_id": "late-command-0001",
            "data": {"type": "ctrl.rtc.signal", "error": "busy", "owner": "audio_playback"},
        }
    )
    transport.emit(
        {
            "type": "sys.nack",
            "code": 1,
            "protocol": "watcher-rtc/1",
            "client_id": session["client_id"],
            "session_id": session["session_id"],
            "command_id": "late-command-0002",
            "data": {"type": "ctrl.rtc.signal", "error": "old_session"},
        }
    )

    assert rtc.snapshot()["last_error"] == "busy"


def test_rtc_start_timeout_survives_a_failed_best_effort_stop() -> None:
    class SilentTransport(FakeTransport):
        def send_device(self, frame: str | bytes) -> Future[None]:
            self.sent.append(frame)
            message = json.loads(frame)
            future: Future[None] = Future()
            if message["type"] == "ctrl.rtc.session.stop":
                future.set_exception(ConnectionError("device channel unavailable"))
            else:
                future.set_result(None)
            return future

    transport = SilentTransport()
    rtc = ApplicationRtc(transport, send_timeout=0.01)  # type: ignore[arg-type]

    with pytest.raises(TimeoutError, match="did not acknowledge"):
        rtc.start()

    assert [json.loads(frame)["type"] for frame in transport.sent] == [
        "ctrl.rtc.session.start",
        "ctrl.rtc.session.stop",
    ]
    assert rtc.snapshot()["active"] is False
    assert rtc.snapshot()["state"] == "failed"
    assert rtc.snapshot()["last_error"] == "start_ack_timeout"


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
    assert rtc.snapshot()["mode"] is None


def test_rtc_stop_waits_for_device_release_ack_before_returning() -> None:
    stop_acknowledged = False

    class DelayedStopTransport(FakeTransport):
        def send_device(self, frame: str | bytes) -> Future[None]:
            nonlocal stop_acknowledged
            message = json.loads(frame)
            if message["type"] != "ctrl.rtc.session.stop":
                return super().send_device(frame)
            self.sent.append(frame)
            future: Future[None] = Future()
            future.set_result(None)

            def acknowledge() -> None:
                nonlocal stop_acknowledged
                stop_acknowledged = True
                self.emit(
                    {
                        "type": "sys.ack",
                        "code": 0,
                        "protocol": message["protocol"],
                        "client_id": message["client_id"],
                        "session_id": message["session_id"],
                        "command_id": message["command_id"],
                        "data": {"type": message["type"]},
                    }
                )

            Timer(0.02, acknowledge).start()
            return future

    rtc = ApplicationRtc(DelayedStopTransport(), send_timeout=0.5)  # type: ignore[arg-type]
    rtc.start()

    assert rtc.stop() is True
    assert stop_acknowledged is True
    assert rtc.snapshot()["active"] is False
    assert rtc.snapshot()["mode"] is None


def test_rtc_terminal_stopped_event_completes_stop_before_late_ack() -> None:
    class StoppedBeforeAckTransport(FakeTransport):
        def send_device(self, frame: str | bytes) -> Future[None]:
            message = json.loads(frame)
            if message["type"] != "ctrl.rtc.session.stop":
                return super().send_device(frame)
            self.sent.append(frame)
            future: Future[None] = Future()
            future.set_result(None)
            self.emit(
                {
                    "type": "evt.rtc.state",
                    "protocol": message["protocol"],
                    "client_id": message["client_id"],
                    "session_id": message["session_id"],
                    "data": {"state": "stopped", "reason": "explicit_stop"},
                }
            )
            return future

    rtc = ApplicationRtc(StoppedBeforeAckTransport(), send_timeout=0.05)  # type: ignore[arg-type]
    rtc.start()

    assert rtc.stop() is True
    assert rtc.snapshot()["state"] == "stopped"
    assert rtc.snapshot()["last_error"] is None


def test_rtc_failed_event_does_not_claim_device_resources_are_released() -> None:
    transport = FakeTransport()
    rtc = ApplicationRtc(transport)  # type: ignore[arg-type]
    session = rtc.start()

    transport.emit(
        {
            "type": "evt.rtc.state",
            "protocol": "watcher-rtc/1",
            "client_id": session["client_id"],
            "session_id": session["session_id"],
            "data": {"state": "failed", "reason": "mjpeg_data_channel_closed"},
        }
    )

    assert rtc.snapshot()["active"] is True
    assert rtc.snapshot()["mode"] == "video"
    assert rtc.snapshot()["last_error"] == "mjpeg_data_channel_closed"


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


def test_rtc_stop_timeout_keeps_session_exclusive_and_allows_retry() -> None:
    class FirstStopSilentTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.stop_attempts = 0

        def send_device(self, frame: str | bytes) -> Future[None]:
            message = json.loads(frame)
            if message["type"] != "ctrl.rtc.session.stop":
                return super().send_device(frame)
            self.stop_attempts += 1
            if self.stop_attempts > 1:
                return super().send_device(frame)
            self.sent.append(frame)
            future: Future[None] = Future()
            future.set_result(None)
            return future

    transport = FirstStopSilentTransport()
    rtc = ApplicationRtc(transport, send_timeout=0.01)  # type: ignore[arg-type]
    rtc.start()

    with pytest.raises(TimeoutError, match="session stop"):
        rtc.stop()

    assert rtc.snapshot()["active"] is True
    assert rtc.snapshot()["mode"] == "video"
    assert rtc.stop() is True
    assert transport.stop_attempts == 2


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
