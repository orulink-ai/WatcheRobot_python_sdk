"""Contracts for the independently distributable LAN intercom."""
from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).parents[1] / "examples" / "robot_intercom"


@pytest.fixture
def rig(tmp_path):
    spec = importlib.util.spec_from_file_location("intercom_service", ROOT / "service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    connection = dict(online=True, device_id="robot-a", connection_id="connection-a",
                      preview_websocket_url="ws://192.168.1.10:80/preview")
    class Rtc:
        active = False
        state = "connected"
        fail_start = False
        fail_stop = False
        def start(self, *, mode):
            calls.append(("rtc", mode))
            if self.fail_start:
                raise TimeoutError("RTC startup unconfirmed")
            self.active = True
            return self.snapshot()
        def stop(self):
            calls.append(("rtc", "stop"))
            if self.fail_stop:
                raise TimeoutError("RTC stop unconfirmed")
            self.active = False
            return True
        def snapshot(self):
            return dict(active=self.active, state=self.state if self.active else "idle", stats={})
        def clock_ping(self, value):
            calls.append(("ping", value))
        def send_offer(self, value):
            calls.append(("offer", value))
        def send_candidate(self, value, **kwargs):
            calls.append(("candidate", value))
        def feedback(self, **kwargs):
            calls.append(("feedback", kwargs))
        def events(self, *, after):
            return []
    def follow(enabled):
        calls.append(("follow", enabled))
    robot = SimpleNamespace(
        device_info={"device_id": "robot-a", "firmware_version": "test"},
        capabilities=("rtc.audio.full_duplex.v1", "expression.audio_follow.v1", "camera.capture"),
        expression_runtime=SimpleNamespace(set_audio_follow=follow),
        camera=SimpleNamespace(capture=lambda **kwargs: SimpleNamespace(data=b"\xff\xd8jpeg\xff\xd9")),
        refresh_device_info=lambda **kwargs: None,
        resource_evidence={},
    )
    clock = [0.0]
    rtc = Rtc()
    service = module.IntercomService(robot=robot, rtc=rtc, device_status=lambda: dict(connection),
                                    artifacts_dir=tmp_path, clock=lambda: clock[0])
    return SimpleNamespace(module=module, service=service, robot=robot, rtc=rtc,
                           calls=calls, connection=connection, clock=clock, path=tmp_path)


def test_start_enables_actual_playback_mouth_before_audio(rig):
    result = rig.service.start("request-a")
    assert result["request_id"] == "request-a"
    assert rig.calls == [("follow", True), ("rtc", "audio")]
    assert rig.service.start("request-a")["request_id"] == "request-a"
    assert len(rig.calls) == 2
    with pytest.raises(rig.module.IntercomError):
        rig.service.start("request-b")


def test_page_open_after_pairing_refreshes_capabilities_before_enabling_start(rig):
    expected = rig.robot.capabilities
    rig.robot.capabilities = ()
    refreshed = []
    def refresh(**kwargs):
        refreshed.append(True)
        rig.robot.capabilities = expected
    rig.robot.refresh_device_info = refresh
    assert set(rig.service.status()["capabilities"]) == set(expected)
    assert len(refreshed) == 1
    rig.service.status()
    assert len(refreshed) == 1


def test_missing_capability_or_offline_never_starts_hardware(rig):
    rig.robot.capabilities = ("rtc.audio.full_duplex.v1",)
    with pytest.raises(rig.module.IntercomError):
        rig.service.start("request-a")
    assert not rig.calls
    rig.connection["online"] = False
    with pytest.raises(rig.module.IntercomError):
        rig.service.capture("request-a")
    assert not list(rig.path.iterdir())


def test_failed_start_releases_mouth_and_retry_is_possible(rig):
    rig.rtc.fail_start = True
    with pytest.raises(TimeoutError):
        rig.service.start("request-a")
    assert rig.calls[-1] == ("follow", False)
    assert rig.service.status()["request_id"] is None
    rig.rtc.fail_start = False
    rig.service.start("request-b")


def test_failed_enable_still_attempts_disable(rig):
    def follow(enabled):
        rig.calls.append(("follow", enabled))
        if enabled:
            raise TimeoutError("ACK lost")
    rig.robot.expression_runtime.set_audio_follow = follow
    with pytest.raises(TimeoutError):
        rig.service.start("request-a")
    assert rig.calls == [("follow", True), ("follow", False)]


def test_photo_does_not_stop_audio_and_uses_unique_files(rig):
    rig.service.start("request-a")
    first = rig.service.capture("request-a")
    second = rig.service.capture("request-a")
    assert first["artifact"] != second["artifact"]
    assert rig.rtc.active
    assert len(rig.calls) == 2
    assert (rig.path / first["artifact"]).read_bytes().startswith(b"\xff\xd8")


def test_stale_tab_cannot_stop_signal_or_capture_new_call(rig):
    rig.service.start("request-a")
    rig.service.stop("request-a")
    rig.service.start("request-b")
    assert rig.service.stop("request-a")["matched"] is False
    with pytest.raises(rig.module.IntercomError):
        rig.service.signal("request-a", {"kind": "offer", "sdp": "offer"})
    with pytest.raises(rig.module.IntercomError):
        rig.service.capture("request-a")
    assert rig.rtc.active


def test_stop_failure_retains_owner_and_blocks_restart_until_retry(rig):
    rig.service.start("request-a")
    rig.rtc.fail_stop = True
    with pytest.raises(TimeoutError):
        rig.service.stop("request-a")
    assert rig.service.status()["request_id"] == "request-a"
    assert ("follow", False) not in rig.calls
    with pytest.raises(rig.module.IntercomError):
        rig.service.start("request-b")
    rig.rtc.fail_stop = False
    rig.service.stop("request-a")
    assert rig.calls[-2:] == [("rtc", "stop"), ("follow", False)]


def test_device_switch_cannot_send_cleanup_to_other_robot(rig):
    rig.service.start("request-a")
    rig.connection["device_id"] = "robot-b"
    rig.robot.device_info["device_id"] = "robot-b"
    before = list(rig.calls)
    with pytest.raises(rig.module.IntercomError):
        rig.service.stop("request-a")
    assert rig.calls == before


def test_new_pairing_request_cannot_receive_old_session_heartbeat(rig):
    rig.connection["request_id"] = "pairing-a"
    rig.service.start("request-a")
    rig.connection["request_id"] = "pairing-b"
    with pytest.raises(rig.module.IntercomError):
        rig.service.heartbeat("request-a", 1000)
    assert not any(call[0] == "ping" for call in rig.calls)


def test_new_pairing_cannot_receive_cleanup_even_when_device_id_is_reused(rig):
    # Firmware may advertise the same generic device ID for multiple robots.
    rig.connection["request_id"] = "pairing-a"
    rig.service.start("request-a")
    before = list(rig.calls)
    rig.connection["request_id"] = "pairing-b"
    with pytest.raises(rig.module.IntercomError):
        rig.service.stop("request-a")
    assert rig.calls == before
    assert rig.service.status()["request_id"] == "request-a"
    # A transient reconnect within the original pairing may safely retry.
    rig.connection["request_id"] = "pairing-a"
    rig.service.stop("request-a")
    assert rig.calls[-2:] == [("rtc", "stop"), ("follow", False)]


def test_stop_waits_for_photo_but_heartbeat_stays_responsive(rig):
    rig.service.start("request-a")
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    def capture(**kwargs):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(data=b"jpeg")
    rig.robot.camera.capture = capture
    photo = threading.Thread(target=lambda: rig.service.capture("request-a"))
    photo.start()
    assert entered.wait(1)
    stop = threading.Thread(target=lambda: (rig.service.stop("request-a"), stopped.set()))
    stop.start()
    rig.service.heartbeat("request-a", 1000)
    assert not stopped.is_set()
    release.set()
    photo.join(3)
    stop.join(3)
    assert stopped.is_set()


def test_browser_crash_expires_session(rig):
    rig.service.start("request-a")
    rig.clock[0] = 20
    rig.service.maintain()
    assert rig.service.status()["request_id"] is None
    assert rig.calls[-2:] == [("rtc", "stop"), ("follow", False)]


def test_device_keepalive_survives_a_short_browser_timer_pause(rig):
    rig.service.start("request-a")
    rig.clock[0] = 4
    rig.service.maintain()
    assert any(call[0] == "ping" for call in rig.calls)
    assert rig.service._last_seen == 0  # Device pings must not renew page ownership.
    rig.clock[0] = 12
    rig.service.maintain()
    assert rig.service.status()["state"] == "idle"


def test_device_keepalive_is_not_blocked_by_photo_or_sent_to_new_pairing(rig):
    rig.connection["request_id"] = "pairing-a"
    rig.service.start("request-a")
    entered, release = threading.Event(), threading.Event()
    def capture(**kwargs):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(data=b"jpeg")
    rig.robot.camera.capture = capture
    photo = threading.Thread(target=lambda: rig.service.capture("request-a"))
    photo.start()
    assert entered.wait(1)
    try:
        rig.clock[0] = 4
        rig.service.maintain()
        assert any(call[0] == "ping" for call in rig.calls)
        rig.connection["request_id"] = "pairing-b"
        before = list(rig.calls)
        rig.clock[0] = 6
        rig.service.maintain()
        assert rig.calls == before
    finally:
        release.set()
        photo.join(3)


def test_browser_presence_survives_timer_suspension_and_disconnect_releases(rig):
    rig.service.start("request-a")
    token = rig.service.attach_presence("request-a")
    rig.clock[0] = 60
    rig.service.maintain()
    assert rig.service.status()["state"] == "running"
    assert any(call[0] == "ping" for call in rig.calls)
    rig.service.detach_presence("request-a", token)
    assert rig.service.status()["state"] == "idle"
    rig.service.start("request-b")
    rig.service.detach_presence("request-a", token)
    assert rig.service.status()["state"] == "running"


def test_presence_rejects_stale_or_duplicate_connections(rig):
    rig.service.start("request-a")
    with pytest.raises(rig.module.IntercomError):
        rig.service.attach_presence("request-b")
    token = rig.service.attach_presence("request-a")
    with pytest.raises(rig.module.IntercomError):
        rig.service.attach_presence("request-a")
    rig.service.detach_presence("request-a", token)


def test_diagnostics_retain_terminal_reason_and_never_record_signal_payload(rig):
    rig.service.start("request-a")
    rig.service.diagnostic("request-a", {"kind": "peer_state", "connection_state": "disconnected", "sdp": "private-signal"})
    with pytest.raises(rig.module.IntercomError):
        rig.service.diagnostic("request-b", {"kind": "peer_state"})
    rig.rtc.events = lambda **kwargs: [{"id": 1, "message": {
        "type": "evt.rtc.state", "data": {"state": "stopped", "reason": "heartbeat_timeout"}}}]
    rig.service.maintain()
    rig.service.stop("request-a", reason="browser_failure")
    diagnostic = rig.service.status()["diagnostics"]
    assert diagnostic["device_end_reason"] == "heartbeat_timeout"
    assert diagnostic["end_reason"] == "device_terminal"
    assert any(e["kind"] == "peer_state" for e in diagnostic["history"])
    import json
    assert "private-signal" not in json.dumps(diagnostic)


def test_presence_websocket_checks_origin_and_cleans_up_on_close(rig):
    with TestClient(rig.module.create_web_app(rig.service, web_root=ROOT / "web")) as client:
        client.post("/api/call/start", json={"request_id": "request-a"})
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/call/presence?request_id=request-a", headers={"Origin": "https://evil.example"}):
                pass
        with client.websocket_connect("/api/call/presence?request_id=request-a", headers={"Origin": "http://testserver"}) as ws:
            assert ws.receive_json() == {"ready": True}
        assert rig.service.status()["state"] == "idle"


def load_local_http():
    spec = importlib.util.spec_from_file_location("intercom_local_http", ROOT / "local_http.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_origin_is_reused_after_restart(tmp_path):
    module = load_local_http()
    state = tmp_path / "http-port.json"
    with module.open_listener(state) as listener:
        original = listener.getsockname()
        assert original[0] == "127.0.0.1"
    with module.open_listener(state) as listener:
        assert listener.getsockname() == original


def test_busy_origin_uses_temporary_port_without_overwriting_preference(tmp_path):
    module = load_local_http()
    state = tmp_path / "http-port.json"
    with module.open_listener(state) as first:
        original = first.getsockname()
        saved = state.read_bytes()
        with module.open_listener(state) as second:
            assert second.getsockname()[1] != original[1]
        assert state.read_bytes() == saved
    with module.open_listener(state) as listener:
        assert listener.getsockname() == original


def test_invalid_origin_preferences_are_replaced_safely(tmp_path):
    module = load_local_http()
    state = tmp_path / "http-port.json"
    for data in ('broken', '{"port":true}', '{"port":1}', '{"port":70000}'):
        state.write_text(data)
        with module.open_listener(state) as listener:
            assert listener.getsockname()[0] == "127.0.0.1"
            assert listener.getsockname()[1] > 1023


def test_stop_before_late_start_cannot_reallocate_hardware(rig):
    assert rig.service.stop("request-a")["state"] == "idle"
    with pytest.raises(rig.module.IntercomError):
        rig.service.start("request-a")
    assert not rig.calls
    rig.service.start("request-b")


def test_online_device_without_pairing_identity_cannot_start_hardware(rig):
    rig.connection.pop("connection_id")
    with pytest.raises(rig.module.IntercomError):
        rig.service.start("request-a")
    assert not rig.calls


def test_slow_offer_within_negotiation_deadline_is_not_expired(rig):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    rig.clock[0] = 11
    rig.service.maintain()
    assert rig.rtc.active
    assert rig.service.status()["request_id"] == "request-a"
    rig.clock[0] = 31
    rig.service.maintain()
    assert not rig.rtc.active
    assert rig.service.status()["request_id"] is None
    with pytest.raises(rig.module.IntercomError):
        rig.service.signal("request-a", {"kind": "offer", "sdp": "late"})


def test_connected_call_switches_to_heartbeat_expiry(rig):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    rig.clock[0] = 15
    rig.rtc.state = "connected"
    rig.service.maintain()
    assert rig.service.status()["state"] == "running"
    rig.clock[0] = 26
    rig.service.maintain()
    assert not rig.rtc.active


def test_presence_cannot_extend_the_negotiation_deadline(rig):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    rig.service.attach_presence("request-a")
    rig.clock[0] = 30
    rig.rtc.state = "connected"
    rig.service.maintain()
    assert not rig.rtc.active
    assert rig.service.status()["state"] == "idle"


def test_presence_keeps_a_timely_connected_call_alive_during_browser_pause(rig):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    rig.service.attach_presence("request-a")
    rig.clock[0] = 29
    rig.rtc.state = "connected"
    rig.service.maintain()
    assert rig.service.status()["state"] == "running"
    rig.clock[0] = 54
    rig.service.maintain()
    assert rig.rtc.active
    assert any(call[0] == "ping" for call in rig.calls)


@pytest.mark.parametrize("observed_at", [30, 31])
def test_connected_snapshot_at_or_after_deadline_is_released(rig, observed_at):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    rig.clock[0] = observed_at
    rig.rtc.state = "connected"
    rig.service.maintain()
    assert not rig.rtc.active
    assert rig.service.status()["request_id"] is None


def test_connection_just_before_deadline_can_run(rig):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    rig.clock[0] = 29.999
    rig.rtc.state = "connected"
    rig.service.maintain()
    assert rig.rtc.active
    assert rig.service.status()["state"] == "running"


def test_blocked_start_cannot_accept_connected_after_deadline(rig):
    original_start = rig.rtc.start
    def delayed_start(**kwargs):
        original_start(**kwargs)
        rig.clock[0] = 30
    rig.rtc.start = delayed_start
    with pytest.raises(rig.module.IntercomError, match="超时"):
        rig.service.start("request-a")
    assert not rig.rtc.active
    assert rig.service.status()["request_id"] is None


@pytest.mark.parametrize("operation", ["signal", "heartbeat", "feedback", "events", "capture", "start"])
def test_expired_negotiation_rejects_requests_before_maintenance(rig, operation):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    before = list(rig.calls)
    rig.clock[0] = 30
    args = {
        "signal": ("request-a", {"kind": "offer", "sdp": "late"}),
        "heartbeat": ("request-a", 1000), "feedback": ("request-a", {}),
        "events": ("request-a", 0), "capture": ("request-a",), "start": ("request-a",),
    }
    with pytest.raises(rig.module.IntercomError, match="超时"):
        getattr(rig.service, operation)(*args[operation])
    assert rig.calls == before
    assert rig.service.status()["state"] == "cleanup_required"
    rig.service.maintain()
    assert not rig.rtc.active


def test_inflight_signal_crossing_deadline_requires_cleanup(rig):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    def slow_offer(value):
        rig.calls.append(("offer", value))
        rig.clock[0] = 31
    rig.rtc.send_offer = slow_offer
    rig.clock[0] = 29
    with pytest.raises(rig.module.IntercomError, match="超时"):
        rig.service.signal("request-a", {"kind": "offer", "sdp": "offer"})
    assert rig.service.status()["state"] == "cleanup_required"
    rig.service.maintain()
    assert not rig.rtc.active


@pytest.mark.parametrize("phase", ["follow", "online"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_start_deadline_before_rtc_does_not_allocate_and_preserves_cleanup(rig, phase, cleanup_fails):
    def follow(enabled):
        rig.calls.append(("follow", enabled))
        if enabled and phase == "follow":
            rig.clock[0] = 30
        if not enabled and cleanup_fails:
            raise TimeoutError("release unconfirmed")
    rig.robot.expression_runtime.set_audio_follow = follow
    original_status = rig.service.device_status
    def status():
        if phase == "online" and ("follow", True) in rig.calls:
            rig.clock[0] = 30
        return original_status()
    rig.service.device_status = status
    with pytest.raises(rig.module.IntercomError, match="超时"):
        rig.service.start("request-a")
    assert not any(call[0] == "rtc" for call in rig.calls)
    assert rig.calls == [("follow", True), ("follow", False)]
    assert rig.service.status()["state"] == ("cleanup_required" if cleanup_fails else "idle")
    if cleanup_fails:
        rig.robot.expression_runtime.set_audio_follow = lambda enabled: rig.calls.append(("follow", enabled))
        rig.service.stop("request-a")
        assert rig.service.status()["state"] == "idle"


@pytest.mark.parametrize("operation", ["signal", "heartbeat", "feedback", "capture", "start"])
def test_device_status_crossing_deadline_cannot_issue_new_operation(rig, operation):
    rig.rtc.state = "connecting"
    rig.service.start("request-a")
    before = list(rig.calls)
    original_capture = rig.robot.camera.capture
    def capture(**kwargs):
        rig.calls.append(("capture", None))
        return original_capture(**kwargs)
    rig.robot.camera.capture = capture
    original_status = rig.service.device_status
    def delayed_status():
        rig.clock[0] = 30
        return original_status()
    rig.service.device_status = delayed_status
    args = {
        "signal": ("request-a", {"kind": "offer", "sdp": "late"}),
        "heartbeat": ("request-a", 1000), "feedback": ("request-a", {}),
        "capture": ("request-a",), "start": ("request-a",),
    }
    with pytest.raises(rig.module.IntercomError, match="超时"):
        getattr(rig.service, operation)(*args[operation])
    assert rig.calls == before
    assert rig.service.status()["state"] == "cleanup_required"
    rig.service.maintain()
    assert not rig.rtc.active


def test_pending_heartbeat_is_not_expired_while_it_holds_signal_lock(rig):
    rig.service.start("request-a")
    entered, release = threading.Event(), threading.Event()
    def ping(value):
        entered.set()
        assert release.wait(3)
    rig.rtc.clock_ping = ping
    thread = threading.Thread(target=lambda: rig.service.heartbeat("request-a", 1000))
    thread.start()
    assert entered.wait(1)
    rig.clock[0] = 11
    rig.service.maintain()
    assert rig.rtc.active
    release.set()
    thread.join(3)
    assert not thread.is_alive()
    rig.service.maintain()
    assert rig.rtc.active


def test_http_rejects_foreign_origin_and_private_files(rig):
    with TestClient(rig.module.create_web_app(rig.service, web_root=ROOT / "web")) as client:
        assert client.get("/").status_code == 200
        body = {"request_id": "request-a"}
        assert client.post("/api/call/start", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/call/start", json=body).status_code == 200
        assert client.get("/photos/app.json").status_code == 404
        assert client.post("/api/call/stop", json=body).status_code == 200
