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
            return dict(active=self.active, state="connected" if self.active else "idle", stats={})
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


def test_http_rejects_foreign_origin_and_private_files(rig):
    with TestClient(rig.module.create_web_app(rig.service, web_root=ROOT / "web")) as client:
        assert client.get("/").status_code == 200
        body = {"request_id": "request-a"}
        assert client.post("/api/call/start", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/call/start", json=body).status_code == 200
        assert client.get("/photos/app.json").status_code == 404
        assert client.post("/api/call/stop", json=body).status_code == 200
