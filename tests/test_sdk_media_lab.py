from __future__ import annotations

import importlib.util
import json
import re
import threading
import wave
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).parents[1]
LAB_ROOT = ROOT / "examples" / "sdk_media_lab"


class FakeFaceTracking:
    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.fail_stop = False
        self.fail_start = False
        self.start_timeout = None

    def start(self, **kwargs):
        self.starts += 1
        self.start_timeout = kwargs.get("timeout")
        if self.fail_start:
            raise TimeoutError("start unconfirmed")

    def stop(self, *, policy, **kwargs):
        assert policy == "hold"
        self.stops += 1
        if self.fail_stop:
            raise TimeoutError("stop unconfirmed")


def test_face_preview_capability_and_lifecycle(tmp_path):
    from dataclasses import dataclass, field
    from types import MappingProxyType
    module = _load_service_module()
    robot = _robot()
    robot.capabilities += ("face_tracking.control.v1",)
    robot.face_tracking = FakeFaceTracking()
    service = _service(module, tmp_path, robot)
    client = _client_for_service(module, tmp_path, service)
    assert client.post("/api/face-tracking/preview/start").status_code == 409
    assert not service.status()["resource_owners"]
    robot.capabilities += ("face_tracking.preview.v1",)
    @dataclass
    class Telemetry:
        age_ms: int = 5
        raw: object = field(default_factory=lambda: MappingProxyType({"seq": 42}))
    frame = SimpleNamespace(jpeg=b"jpeg", sequence=42, width=640, height=480,
                            faces=(), telemetry=Telemetry())
    calls = []
    def open_preview(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(read=lambda **kw: frame)
    robot.face_tracking.open_preview = open_preview
    assert client.post("/api/face-tracking/preview/start").status_code == 200
    assert service.status()["face_tracking"]["preview_receiving"] is False
    assert calls == [dict(width=640, height=480, frame_stride=1, stop_policy="hold", queue_size=1)]
    assert client.post("/api/face-tracking/start").status_code == 409
    data = client.get("/api/face-tracking/preview/frame").json()
    assert data["sequence"] == 42 and data["jpeg_base64"] == "anBlZw=="
    assert data["telemetry"] == {"age_ms": 5}
    assert service.status()["face_tracking"]["preview_receiving"] is True
    robot.face_tracking.fail_stop = True
    assert client.post("/api/face-tracking/stop").status_code >= 400
    assert "camera" in service.status()["resource_owners"]
    robot.face_tracking.fail_stop = False
    assert client.post("/api/face-tracking/stop").status_code == 200
    assert client.get("/api/face-tracking/preview/frame").status_code == 409
    assert not service.status()["resource_owners"]


def test_face_tracking_controls_hold_camera_and_motion_until_stop(tmp_path):
    module = _load_service_module()
    robot = _robot()
    robot.capabilities += ("face_tracking.control.v1",)
    robot.face_tracking = FakeFaceTracking()
    service = _service(module, tmp_path, robot)
    client = _client_for_service(module, tmp_path, service)
    assert client.post("/api/face-tracking/start").status_code == 200
    assert client.post("/api/face-tracking/start").status_code == 200
    assert robot.face_tracking.starts == 1
    # A cold device verifies its model catalog before starting the camera.
    assert robot.face_tracking.start_timeout == 10.0
    assert service.status()["resource_owners"]["camera"] == "face_tracking"
    with pytest.raises(module.MediaLabBusyError):
        service.capture_photo()
    robot.face_tracking.fail_stop = True
    with pytest.raises(TimeoutError):
        service.stop_face_tracking()
    assert service.status()["face_tracking"]["state"] == "stop_required"
    assert "motion" in service.status()["resource_owners"]
    robot.face_tracking.fail_stop = False
    assert client.post("/api/face-tracking/stop").status_code == 200
    assert not service.status()["resource_owners"]
    assert service.status()["face_tracking"]["state"] == "idle"


def test_face_tracking_unsupported_never_sends_start(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path)
    client = _client_for_service(module, tmp_path, service)
    assert client.post("/api/face-tracking/start").status_code == 409
    assert not service.status()["resource_owners"]


def test_face_tracking_failed_start_is_stopped_before_releasing_resources(tmp_path):
    module = _load_service_module()
    robot = _robot()
    robot.capabilities += ("face_tracking.control.v1",)
    robot.face_tracking = FakeFaceTracking()
    robot.face_tracking.fail_start = True
    service = _service(module, tmp_path, robot)
    with pytest.raises(TimeoutError):
        service.start_face_tracking()
    assert robot.face_tracking.stops == 1
    assert not service.status()["resource_owners"]


def test_face_tracking_application_shutdown_stops_owned_tracking(tmp_path):
    module = _load_service_module()
    robot = _robot()
    robot.capabilities += ("face_tracking.control.v1",)
    robot.face_tracking = FakeFaceTracking()
    service = _service(module, tmp_path, robot)
    with _client_for_service(module, tmp_path, service) as client:
        assert client.post("/api/face-tracking/start").status_code == 200
    assert robot.face_tracking.stops == 1
    assert not service.status()["resource_owners"]


def test_face_tracking_uncertain_start_and_stop_keep_resource_ownership(tmp_path):
    module = _load_service_module()
    robot = _robot()
    robot.capabilities += ("face_tracking.control.v1",)
    robot.face_tracking = FakeFaceTracking()
    robot.face_tracking.fail_start = robot.face_tracking.fail_stop = True
    service = _service(module, tmp_path, robot)
    with pytest.raises(TimeoutError):
        service.start_face_tracking()
    assert service.status()["face_tracking"]["state"] == "stop_required"
    with pytest.raises(module.MediaLabBusyError):
        service.start_face_tracking()
    assert set(service.status()["resource_owners"]) == {"camera", "motion"}
    robot.face_tracking.fail_stop = False
    service.stop_face_tracking()


def test_face_tracking_disconnect_requires_stop_on_reconnection(tmp_path):
    module = _load_service_module()
    robot = _robot()
    robot.capabilities += ("face_tracking.control.v1",)
    robot.face_tracking = FakeFaceTracking()
    service = _service(module, tmp_path, robot)
    service.start_face_tracking()
    service._device_status_provider = lambda: {"online": False}
    service.maintain()
    assert service.status()["face_tracking"]["state"] == "stop_required"
    service._device_status_provider = lambda: {"online": True}
    service.maintain()
    assert robot.face_tracking.stops == 1
    assert robot.face_tracking.starts == 1
    assert not service.status()["resource_owners"]


def _load_service_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "watcherobot_sdk_media_lab_service",
        LAB_ROOT / "service.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePlayback:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.wait_calls: list[float] = []

    def wait(self, timeout: float) -> None:
        self.wait_calls.append(timeout)
        if self.gate is not None:
            assert self.gate.wait(timeout=1.0)


class FakeAudio:
    def __init__(self, playback: FakePlayback) -> None:
        self.playback = playback
        self.paths: list[Path] = []
        self.stop_calls = 0

    def play_file(self, path: Path) -> FakePlayback:
        self.paths.append(Path(path))
        return self.playback

    def stop(self) -> None:
        self.stop_calls += 1


class FakeCamera:
    def __init__(self) -> None:
        self.calls: list[dict[str, int | float]] = []
        self.feedback_calls: list[dict[str, int | float]] = []

    def capture(self, **kwargs: int | float) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(data=b"\xff\xd8media-lab\xff\xd9")

    def capture_with_feedback(self, **kwargs: int | float) -> SimpleNamespace:
        self.feedback_calls.append(kwargs)
        return SimpleNamespace(data=b"\xff\xd8media-lab-feedback\xff\xd9")


class FakeMicrophone:
    def __init__(self) -> None:
        self.calls: list[dict[str, float | int]] = []

    def record_pcm(self, **kwargs: float | int) -> SimpleNamespace:
        self.calls.append(kwargs)
        frame_count = round(16000 * float(kwargs["duration"]))
        return SimpleNamespace(
            data=b"\x00\x00" * frame_count,
            format=SimpleNamespace(
                channels=1,
                sample_width_bytes=2,
                sample_rate_hz=16000,
                encoding="pcm_s16le",
            ),
            duration_seconds=frame_count / 16000,
            dropped_frames=0,
            decode_failures=0,
        )


class FakeJob:
    def __init__(self, operation_id: int) -> None:
        self.id = operation_id
        self.wait_calls: list[float] = []

    def wait(self, timeout: float) -> "FakeJob":
        self.wait_calls.append(timeout)
        return self


class FakeMotion:
    def __init__(self) -> None:
        self.moves: list[dict[str, int | str]] = []
        self.stop_calls = 0
        self.job = FakeJob(101)

    def move_to(self, **kwargs: int | str) -> FakeJob:
        self.moves.append(kwargs)
        return self.job

    def stop(self) -> None:
        self.stop_calls += 1


class FakeLights:
    def __init__(self) -> None:
        self.colors: list[tuple[str, float, str]] = []
        self.effects: list[dict[str, object]] = []
        self.off_calls = 0
        self.job = FakeJob(202)

    def set_color(self, color: str, *, brightness: float, zone: str) -> None:
        self.colors.append((color, brightness, zone))

    def play_effect(self, effect: str, **kwargs: object) -> FakeJob:
        self.effects.append({"effect": effect, **kwargs})
        return self.job

    def off(self) -> None:
        self.off_calls += 1


class FakeAnimation:
    def __init__(self) -> None:
        self.played: list[str] = []
        self.prefetched: list[str] = []
        self.stop_calls = 0
        self.job = FakeJob(303)
        self.available_ids = ("boot", "happy", "thinking", "standby_little4")

    def play(self, animation_id: str) -> FakeJob:
        self.played.append(animation_id)
        return self.job

    def prefetch(self, animation_id: str) -> None:
        self.prefetched.append(animation_id)

    def stop(self) -> None:
        self.stop_calls += 1


class FakeRtc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self._state = {
            "active": False,
            "client_id": None,
            "session_id": None,
            "state": "idle",
            "mode": None,
            "last_error": None,
            "capabilities": {},
            "stats": {},
        }

    def start(self, *, mode: str = "video") -> dict[str, object]:
        self.calls.append(("start", mode))
        self._state.update(
            active=True,
            client_id="client-0001",
            session_id="session-0001",
            state="starting",
            mode=mode,
        )
        return self.snapshot()

    def send_offer(self, sdp: str) -> None:
        self.calls.append(("offer", sdp))

    def send_candidate(self, candidate: str, *, sdp_mid: str, sdp_mline_index: int) -> None:
        self.calls.append(("candidate", (candidate, sdp_mid, sdp_mline_index)))

    def clock_ping(self, browser_send_us: int) -> None:
        self.calls.append(("clock_ping", browser_send_us))

    def feedback(self, **metrics: int) -> None:
        self.calls.append(("feedback", metrics))

    def stop(self) -> bool:
        self.calls.append(("stop", None))
        self._state.update(active=False, state="stopping")
        return True

    def reset(self, *, reason: str) -> bool:
        self.calls.append(("reset", reason))
        was_active = bool(self._state["active"])
        self._state.update(active=False, state="failed", last_error=reason)
        return was_active

    def snapshot(self) -> dict[str, object]:
        return dict(self._state)

    def events(self, *, after: int = 0) -> list[dict[str, object]]:
        return [
            {
                "id": 2,
                "message": {
                    "type": "evt.rtc.signal",
                    "data": {"kind": "answer", "sdp": "v=0\r\n"},
                },
            }
        ] if after < 2 else []


def _robot(*, playback: FakePlayback | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        refresh_device_info=lambda **kwargs: {},
        capabilities=(
            "motion",
            "light",
            "animation",
            "animation.prefetch.v1",
            "audio.stream",
            "microphone",
            "camera.capture",
            "camera.capture.feedback.v1",
            "rtc.audio.full_duplex.v1",
            "rtc.video.mjpeg.v1",
        ),
        device_info={"firmware_version": "V3.1", "device_id": "watcher-test"},
        resource_baseline={
            "sequence": 1,
            "stage": "baseline",
            "captured_at_ms": 100,
            "memory": {
                "internal": {
                    "free_bytes": 128000,
                    "largest_free_block_bytes": 64000,
                }
            },
        },
        resource_snapshot={
            "sequence": 7,
            "stage": "periodic",
            "captured_at_ms": 1234,
            "memory": {
                "internal": {
                    "free_bytes": 48000,
                    "largest_free_block_bytes": 24000,
                    "minimum_free_bytes": 12000,
                }
            },
            "resources": {"rtc": False, "media_system": False},
            "release": {"complete": True, "failures": []},
        },
        resource_rtc_baseline={
            "sequence": 5,
            "stage": "rtc_pre_start",
            "captured_at_ms": 800,
            "memory": {
                "internal": {
                    "free_bytes": 50000,
                    "largest_free_block_bytes": 26000,
                }
            },
        },
        resource_history=[],
        audio=FakeAudio(playback or FakePlayback()),
        camera=FakeCamera(),
        microphone=FakeMicrophone(),
        motion=FakeMotion(),
        lights=FakeLights(),
        animation=FakeAnimation(),
    )


def test_status_exposes_device_resource_snapshot_outside_rtc(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)

    status = service.status()

    assert status["resources"] == {
        "baseline": service._robot.resource_baseline,
        "rtc_baseline": service._robot.resource_rtc_baseline,
        "current": service._robot.resource_snapshot,
        "generation": None,
        "history": service._robot.resource_history,
        "telemetry": {"status": "available", "age_seconds": pytest.approx(0.0, abs=0.1)},
    }
    assert status["rtc"]["stats"] == {}
    assert status["animations"] == ["boot", "happy", "thinking", "standby_little4"]


class FakeProceduralExpression:
    def __init__(self):
        self.calls = []
        self.fail_enable = False
        self.fail_disable = False

    def set_audio_follow(self, enabled):
        self.calls.append(enabled)
        if (enabled and self.fail_enable) or (not enabled and self.fail_disable):
            raise TimeoutError("audio follow command unconfirmed")


def _procedural_robot():
    robot = _robot()
    robot.capabilities += ("expression.audio_follow.v1",)
    robot.expression_runtime = FakeProceduralExpression()
    return robot


class FakeBaselineBehavior:
    def __init__(self):
        self.played = []
        self.stop_calls = 0
        self.fail_start = False
        self.fail_stop = False

    def play(self, behavior_id, *, repeat=1):
        self.played.append((behavior_id, repeat))
        if self.fail_start:
            raise TimeoutError("behavior start unconfirmed")
        return FakeJob(404)

    def stop(self):
        self.stop_calls += 1
        if self.fail_stop:
            raise TimeoutError("behavior stop unconfirmed")


def _sd_baseline_robot():
    robot = _procedural_robot()
    robot.capabilities += ("behavior",)
    robot.animation.available_ids += ("standby",)
    robot.behavior = FakeBaselineBehavior()
    return robot


def test_sd_baseline_http_owns_fixed_animation_until_confirmed_stop(tmp_path):
    module = _load_service_module()
    robot = _sd_baseline_robot()
    service = _service(module, tmp_path, robot)
    client = _client_for_service(module, tmp_path, service)
    started = client.post("/api/controls/sd-baseline/start")
    assert started.status_code == 200
    assert started.json() == {
        "supported": True, "state": "running", "behavior_id": "desktop_expression_panel",
        "animation_id": "standby", "operation_id": 404,
    }
    assert client.post("/api/controls/sd-baseline/start").json() == started.json()
    assert robot.behavior.played == [("desktop_expression_panel", 1)]
    assert service.status()["resource_owners"] == {"animation": "sd_baseline"}
    for action in (service.start_procedural, service.stop_animation):
        with pytest.raises(module.MediaLabBusyError):
            action()
    with pytest.raises(module.MediaLabBusyError):
        service.play_animation(animation_id="boot")
    stopped = client.post("/api/controls/sd-baseline/stop")
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "idle"
    assert stopped.json()["operation_id"] is None
    assert robot.behavior.stop_calls == 1
    assert not service.status()["resource_owners"]
    service.start_procedural()
    with pytest.raises(module.MediaLabBusyError):
        service.start_sd_baseline()


@pytest.mark.parametrize("rtc_first", [False, True])
def test_sd_baseline_coexists_with_full_duplex_rtc_and_recording(tmp_path, rtc_first):
    module = _load_service_module()
    robot = _sd_baseline_robot()
    service = _service(module, tmp_path, robot)
    if rtc_first:
        service.start_live_video(mode="audio")
    service.start_sd_baseline()
    if not rtc_first:
        service.start_live_video(mode="audio")
    service.start_scenario_recording(label="sd-rtc")
    assert service.status()["resource_owners"] == {
        "animation": "sd_baseline", "microphone": "rtc_audio", "speaker": "rtc_audio",
    }
    assert service.scenario_report()["samples"][0]["sd_baseline"]["animation_id"] == "standby"
    service.stop_sd_baseline()
    assert service.status()["rtc"]["active"] is True
    assert service.status()["resource_owners"] == {"microphone": "rtc_audio", "speaker": "rtc_audio"}
    service.stop_live_video()
    assert not service.status()["resource_owners"]


@pytest.mark.parametrize("uncertain_start", [False, True])
def test_sd_baseline_unconfirmed_stop_retains_lease_for_retry(tmp_path, uncertain_start):
    module = _load_service_module()
    robot = _sd_baseline_robot()
    service = _service(module, tmp_path, robot)
    robot.behavior.fail_start = uncertain_start
    if not uncertain_start:
        service.start_sd_baseline()
    robot.behavior.fail_stop = True
    with pytest.raises(TimeoutError):
        service.start_sd_baseline() if uncertain_start else service.stop_sd_baseline()
    assert robot.behavior.stop_calls == 1
    assert service.status()["sd_baseline"]["state"] == "stop_required"
    assert service.status()["resource_owners"] == {"animation": "sd_baseline"}
    with pytest.raises(module.MediaLabBusyError):
        service.start_sd_baseline()
    with pytest.raises(module.MediaLabBusyError):
        service.start_procedural()
    robot.behavior.fail_stop = False
    service.stop_sd_baseline()
    assert robot.behavior.stop_calls == 2
    assert service.status()["sd_baseline"]["state"] == "idle"
    assert not service.status()["resource_owners"]


def test_sd_baseline_shutdown_stops_rtc_before_display(tmp_path):
    module = _load_service_module()
    robot = _sd_baseline_robot()
    rtc = FakeRtc()
    service = _service(module, tmp_path, robot, rtc=rtc)
    shutdown_order = []
    stop_rtc, stop_behavior = rtc.stop, robot.behavior.stop

    def tracked_rtc_stop():
        shutdown_order.append("rtc")
        return stop_rtc()

    def tracked_behavior_stop():
        shutdown_order.append("sd_baseline")
        stop_behavior()

    rtc.stop = tracked_rtc_stop
    robot.behavior.stop = tracked_behavior_stop
    with _client_for_service(module, tmp_path, service) as client:
        assert client.post("/api/controls/sd-baseline/start").status_code == 200
        service.start_live_video(mode="audio")
    assert shutdown_order == ["rtc", "sd_baseline"]
    assert service.status()["sd_baseline"]["state"] == "idle"
    assert not service.status()["resource_owners"]


@pytest.mark.parametrize("api_prefix", ["rtc", "video"])
def test_scoped_rtc_stop_cannot_stop_later_external_session(tmp_path, api_prefix):
    module = _load_service_module()
    service = _service(module, tmp_path)
    client = _client_for_service(module, tmp_path, service)
    prefix = f"/api/{api_prefix}/session"
    old_request = "browser-request-0001"
    assert client.post(f"{prefix}/start", json={"mode": "audio", "request_id": old_request}).status_code == 200
    assert client.post(f"{prefix}/stop", json={"request_id": old_request}).json() == {"stopped": True}
    assert client.post(f"{prefix}/start", json={"mode": "audio"}).status_code == 200
    calls_before = list(service._rtc.calls)
    late_stop = client.post(f"{prefix}/stop", json={"request_id": old_request})
    assert late_stop.status_code == 200
    assert late_stop.json() == {"stopped": False, "matched": False}
    assert service._rtc.calls == calls_before
    assert service.status()["rtc"]["active"] is True
    assert service.status()["resource_owners"] == {"microphone": "rtc_audio", "speaker": "rtc_audio"}
    assert client.post(f"{prefix}/stop").json() == {"stopped": True}


def test_scoped_rtc_stop_timeout_preserves_owner_for_confirmed_retry(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path)
    client = _client_for_service(module, tmp_path, service)
    request_id = "browser-request-0002"
    assert client.post("/api/rtc/session/start", json={"mode": "audio", "request_id": request_id}).status_code == 200
    original_stop = service._rtc.stop

    def unconfirmed_stop():
        raise TimeoutError("RTC stop unconfirmed")

    service._rtc.stop = unconfirmed_stop
    assert client.post("/api/rtc/session/stop", json={"request_id": request_id}).status_code == 502
    assert service.status()["resource_owners"] == {"microphone": "rtc_audio", "speaker": "rtc_audio"}
    service._rtc.stop = original_stop
    assert client.post("/api/rtc/session/stop", json={"request_id": request_id}).json() == {"stopped": True}
    assert not service.status()["resource_owners"]
    assert client.post("/api/rtc/session/start", json={"mode": "audio", "request_id": "new-browser-request"}).status_code == 200
    assert client.post("/api/rtc/session/stop", json={"request_id": request_id}).json() == {"stopped": False, "matched": False}
    assert service.status()["rtc"]["active"] is True


def test_cancelled_microphone_attempt_has_no_rtc_stop_ownership(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path)
    client = _client_for_service(module, tmp_path, service)
    service.start_live_video(mode="audio")
    cancelled = client.post("/api/rtc/session/stop", json={"request_id": "no-device-start-request"})
    assert cancelled.json() == {"stopped": False, "matched": False}
    assert service.status()["rtc"]["active"] is True
    assert ("stop", None) not in service._rtc.calls


def test_sd_baseline_disconnect_requires_stop_before_reuse(tmp_path):
    module = _load_service_module()
    robot = _sd_baseline_robot()
    service = _service(module, tmp_path, robot)
    service.start_sd_baseline()
    service._device_status_provider = lambda: {"online": False}
    service.maintain()
    assert service.status()["sd_baseline"]["state"] == "stop_required"
    assert service.status()["resource_owners"] == {"animation": "sd_baseline"}
    service._device_status_provider = lambda: {"online": True}
    service.maintain()
    assert robot.behavior.stop_calls == 1
    assert service.status()["sd_baseline"]["state"] == "idle"
    assert not service.status()["resource_owners"]


@pytest.mark.parametrize("missing", ["behavior", "standby"])
def test_sd_baseline_requires_firmware_capability_and_fixed_asset(tmp_path, missing):
    module = _load_service_module()
    robot = _sd_baseline_robot()
    if missing == "behavior":
        robot.capabilities = tuple(cap for cap in robot.capabilities if cap != "behavior")
    else:
        robot.animation.available_ids = tuple(asset for asset in robot.animation.available_ids if asset != "standby")
    service = _service(module, tmp_path, robot)
    client = _client_for_service(module, tmp_path, service)
    assert service.status()["sd_baseline"]["supported"] is False
    expected_status = 409 if missing == "behavior" else 422
    assert client.post("/api/controls/sd-baseline/start").status_code == expected_status
    assert not robot.behavior.played
    assert not service.status()["resource_owners"]


def test_procedural_scene_allows_audio_rtc_and_plain_photo_together(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    service.start_procedural()
    service.start_live_video(mode="audio")
    assert service.capture_photo()["bytes"] > 0
    assert service.status()["resource_owners"] == {
        "animation": "procedural", "microphone": "rtc_audio", "speaker": "rtc_audio"
    }
    assert robot.expression_runtime.calls == [True]
    assert service.status()["procedural"]["state"] == "running"
    assert service.status()["procedural"]["telemetry_available"] is False
    with pytest.raises(module.MediaLabBusyError):
        service.play_animation(animation_id="boot")
    with pytest.raises(module.MediaLabBusyError):
        service.stop_animation()
    service.stop_procedural()
    assert robot.expression_runtime.calls == [True, False]
    assert "animation" not in service.status()["resource_owners"]


def test_feedback_capture_cannot_replace_a_running_live_expression(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    service.start_procedural()
    with pytest.raises(module.MediaLabBusyError, match="procedural"):
        service.capture_photo_with_feedback()
    assert robot.camera.feedback_calls == []
    assert service.status()["resource_owners"] == {"animation": "procedural"}
    assert service.capture_photo()["bytes"] > 0
    service.stop_procedural()
    assert service.capture_photo_with_feedback()["artifact"] == "camera-feedback.jpg"


def test_procedural_unsupported_does_not_send_commands(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path)
    client = _client_for_service(module, tmp_path, service)
    result = client.post("/api/controls/procedural/start")
    assert result.status_code == 409
    assert result.json()["capability"] == "expression.audio_follow.v1"
    assert service.status()["procedural"]["supported"] is False
    assert not service.status()["resource_owners"]


def test_procedural_uncertain_start_and_stop_hold_lease_until_retry(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    robot.expression_runtime.fail_enable = True
    robot.expression_runtime.fail_disable = True
    service = _service(module, tmp_path, robot)
    with pytest.raises(TimeoutError):
        service.start_procedural()
    assert robot.expression_runtime.calls == [True, False]
    assert service.status()["procedural"]["state"] == "stop_required"
    assert service.status()["resource_owners"] == {"animation": "procedural"}
    with pytest.raises(module.MediaLabBusyError):
        service.start_procedural()
    robot.expression_runtime.fail_disable = False
    service.stop_procedural()
    assert not service.status()["resource_owners"]


def test_procedural_disconnect_stops_on_reconnect_and_application_shutdown(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    service.start_procedural()
    service._device_status_provider = lambda: {"online": False}
    service.maintain()
    assert service.status()["procedural"]["state"] == "stop_required"
    service._device_status_provider = lambda: {"online": True}
    service.maintain()
    assert robot.expression_runtime.calls == [True, False]
    with _client_for_service(module, tmp_path, service) as client:
        assert client.post("/api/controls/procedural/start").status_code == 200
    assert robot.expression_runtime.calls == [True, False, True, False]


def test_procedural_video_still_conflicts_with_camera(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path, _procedural_robot())
    service.start_procedural()
    service.start_live_video(mode="av")
    with pytest.raises(module.MediaLabBusyError):
        service.capture_photo()


def test_procedural_camera_io_does_not_block_status_and_recording(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    service.start_procedural()
    service.start_scenario_recording()
    entered, release, status_ready = threading.Event(), threading.Event(), threading.Event()
    original = robot.camera.capture

    def blocked_capture(**kwargs):
        entered.set()
        assert release.wait(timeout=2.0)
        return original(**kwargs)

    robot.camera.capture = blocked_capture
    photo_thread = threading.Thread(target=service.capture_photo)
    photo_thread.start()
    assert entered.wait(timeout=1.0)

    def inspect_during_capture():
        status = service.status()
        assert status["resource_owners"]["camera"] == "capture_photo"
        service.maintain()
        status_ready.set()

    status_thread = threading.Thread(target=inspect_during_capture)
    status_thread.start()
    try:
        assert status_ready.wait(timeout=1.0)
    finally:
        release.set()
        photo_thread.join(timeout=2.0)
        status_thread.join(timeout=2.0)
    assert not photo_thread.is_alive()


@pytest.mark.parametrize("scoped_cleanup", [False, True])
def test_inflight_procedural_photo_prevents_display_replacement_until_completion(tmp_path, scoped_cleanup):
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    service.start_procedural(request_id="photo-page-owner")
    entered, release = threading.Event(), threading.Event()
    original = robot.camera.capture
    errors = []

    def blocked_capture(**kwargs):
        entered.set()
        assert release.wait(timeout=3.0)
        return original(**kwargs)

    def capture():
        try:
            service.capture_photo()
        except Exception as error:
            errors.append(error)

    robot.camera.capture = blocked_capture
    thread = threading.Thread(target=capture)
    thread.start()
    assert entered.wait(timeout=1.0)
    try:
        with pytest.raises(module.MediaLabBusyError, match="photo"):
            service.stop_procedural(request_id="photo-page-owner" if scoped_cleanup else None)
        assert service.status()["procedural"]["state"] == ("stop_required" if scoped_cleanup else "running")
        service.maintain()
        assert robot.expression_runtime.calls == [True]
    finally:
        release.set()
        thread.join(timeout=3.0)
    assert not thread.is_alive() and not errors
    if scoped_cleanup:
        service.maintain()
        assert service.procedural_status()["state"] == "idle"
        assert service.status()["resource_owners"] == {}
    else:
        assert service.procedural_status()["state"] == "running"
        assert service.stop_procedural()["state"] == "idle"


def test_procedural_start_reports_whether_it_acquired_the_display(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    assert service.start_procedural()["started"] is True
    assert service.start_procedural()["started"] is False
    assert robot.expression_runtime.calls == [True]


def test_inactive_scenario_sampler_does_not_collect_or_poll(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path, _procedural_robot())

    def unexpected_poll():
        pytest.fail("inactive recording must not poll the daemon")

    service._device_status_provider = unexpected_poll
    service._sample_scenario()


def test_scenario_sampler_verifies_supplied_connection_once(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    robot.resource_snapshot["animation"] = {
        "audio_follow": True, "mouth_level_milli": 123, "pcm_frames": 1,
        "source": "rtc_playback",
    }
    service = _service(module, tmp_path, robot)
    service.start_scenario_recording()
    service._recording_last_sample_at = None

    polls = []
    service._device_status_provider = lambda: polls.append(True) or {"online": True}
    service._sample_scenario(connection={"online": True})
    assert service.scenario_recording_status()["sample_count"] == 2
    assert len(polls) == 1


@pytest.mark.parametrize("collect_status", [False, True])
def test_connection_change_during_evidence_copy_invalidates_telemetry(tmp_path, collect_status):
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    connection = {"online": True, "request_id": "first", "connection_id": "old"}
    service._device_status_provider = lambda: connection
    capture = service._capture_resource_evidence

    def capture_then_reconnect():
        evidence = capture()
        connection.update(request_id="second", connection_id="new")
        return evidence

    service._capture_resource_evidence = capture_then_reconnect
    if collect_status:
        status = service.status()
        assert status["connection"]["connection_id"] == "new"
        assert status["resources"]["telemetry"]["status"] == "unavailable"
        assert status["resources"]["current"] == {}
    else:
        service.start_scenario_recording()
        report = service.scenario_report()
        assert report["samples"][0]["connection"]["connection_id"] == "new"
        assert report["samples"][0]["telemetry"]["status"] == "unavailable"
        assert report["summary"]["memory"] == {}


@pytest.mark.parametrize("change_device", [False, True])
def test_recording_disables_global_memory_summary_after_source_change(tmp_path, monkeypatch, change_device):
    module = _load_service_module()
    robot = _robot()
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    robot.resource_snapshot_received_at = clock[0]
    robot.resource_snapshot = {"sequence": 1, "memory": {"internal": {"free_bytes": 100}}}
    connection = {"online": True, "request_id": "first", "connection_id": "old"}
    service = _service(module, tmp_path, robot)
    service._device_status_provider = lambda: connection
    service.start_scenario_recording()
    assert service.scenario_report()["summary"]["memory"]["internal"]["free_bytes_min"] == 100
    clock[0] += 1.1
    connection.update(request_id="second", connection_id="new")
    if change_device:
        robot.device_info = {"device_id": "other-device"}
    robot.resource_snapshot_received_at = clock[0]
    robot.resource_snapshot = {"sequence": 2, "memory": {"internal": {"free_bytes": 20}}}
    service.sample_scenario()
    report = service.scenario_report()
    assert report["samples"][-1]["telemetry"]["status"] == "available"
    assert report["samples"][-1]["resources"]["memory"]["internal"]["free_bytes"] == 20
    assert report["summary"]["mixed_sources"] is True
    assert report["summary"]["baseline_comparable"] is False
    assert report["summary"]["memory"] == {}


@pytest.mark.parametrize("during_baseline_copy", [False, True])
def test_recording_binds_initial_connection_before_first_sample(tmp_path, monkeypatch, during_baseline_copy):
    module = _load_service_module()
    robot = _robot()
    connection = {"online": True, "request_id": "first", "connection_id": "old"}
    service = _service(module, tmp_path, robot)
    service._device_status_provider = lambda: connection

    def reconnect():
        connection.update(request_id="second", connection_id="new")
        robot.resource_snapshot = {"sequence": 20, "memory": {"internal": {"free_bytes": 20}}}

    if during_baseline_copy:
        copy = module.deepcopy

        def copy_then_reconnect(value):
            result = copy(value)
            if value is robot.resource_baseline:
                reconnect()
            return result

        monkeypatch.setattr(module, "deepcopy", copy_then_reconnect)
    else:
        sample = service._sample_scenario

        def reconnect_before_sample(**kwargs):
            reconnect()
            sample(**kwargs)

        service._sample_scenario = reconnect_before_sample
    service.start_scenario_recording()
    report = service.scenario_report()
    assert report["summary"]["mixed_sources"] is True
    assert report["summary"]["baseline_comparable"] is False
    assert report["summary"]["memory"] == {}


@pytest.mark.parametrize("baseline_generation", [1, 2])
def test_recording_only_compares_baseline_from_current_sdk_generation(tmp_path, baseline_generation):
    module = _load_service_module()
    robot = _robot()
    robot.resource_evidence = {
        "snapshot": robot.resource_snapshot, "received_at": None,
        "device_id": "watcher-test", "generation": 2, "consistent": True,
        "baseline_generation": baseline_generation, "baseline_device_id": "watcher-test",
    }
    service = _service(module, tmp_path, robot)
    service.start_scenario_recording()
    assert service.scenario_report()["summary"]["baseline_comparable"] is (baseline_generation == 2)


@pytest.mark.parametrize("failing_reader", ["resource", "rtc"])
def test_stopping_recording_survives_final_sample_failure(tmp_path, failing_reader):
    module = _load_service_module()
    service = _service(module, tmp_path)
    service.start_scenario_recording()
    initial = service.scenario_report()["samples"]
    service._recording_last_sample_at = None

    def fail():
        raise RuntimeError("diagnostic reader unavailable")

    if failing_reader == "resource":
        service._capture_resource_evidence = fail
    else:
        service._rtc.snapshot = fail
    assert service.stop_scenario_recording()["active"] is False
    assert service.scenario_report()["samples"] == initial
    assert service.scenario_report()["final_sample_error"] == "unavailable"


def test_late_final_sample_error_cannot_annotate_new_recording(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path)
    service.start_scenario_recording(label="old")
    service._recording_last_sample_at = None
    entered, release = threading.Event(), threading.Event()
    snapshot = service._rtc.snapshot

    def fail_old_snapshot():
        if threading.current_thread().name == "old-stopper":
            entered.set()
            assert release.wait(timeout=3.0)
            raise RuntimeError("old final sample failed")
        return snapshot()

    service._rtc.snapshot = fail_old_snapshot
    worker = threading.Thread(target=service.stop_scenario_recording, name="old-stopper")
    worker.start()
    assert entered.wait(timeout=1.0)
    try:
        service.stop_scenario_recording()
        service.start_scenario_recording(label="new")
    finally:
        release.set()
        worker.join(timeout=3.0)
    assert not worker.is_alive()
    report = service.scenario_report()
    assert report["active"] is True and report["label"] == "new"
    assert "final_sample_error" not in report


def test_stable_new_connection_cannot_refresh_old_sdk_evidence_when_identity_refresh_fails(tmp_path):
    module = _load_service_module()
    robot = _robot()
    service = _service(module, tmp_path, robot)
    service._refreshed_connection_token = "old-connection"
    service._device_status_provider = lambda: {"online": True, "request_id": "new-connection"}

    def fail(**kwargs):
        raise RuntimeError("new device has not replied")

    robot.refresh_device_info = fail
    status = service.status()
    assert status["resources"]["telemetry"]["status"] == "unavailable"
    assert status["resources"]["current"] == {}


def test_recording_metadata_failure_preserves_previous_finished_report(tmp_path, monkeypatch):
    module = _load_service_module()
    service = _service(module, tmp_path)
    service.start_scenario_recording(label="previous")
    service.stop_scenario_recording()
    before = service.scenario_report()
    copy = module.deepcopy

    def fail_baseline(value):
        if value is service._robot.resource_baseline:
            raise RuntimeError("baseline unavailable")
        return copy(value)

    monkeypatch.setattr(module, "deepcopy", fail_baseline)
    with pytest.raises(RuntimeError, match="baseline unavailable"):
        service.start_scenario_recording(label="failed")
    after = service.scenario_report()
    assert after["active"] is False
    for field in ("label", "samples", "device", "baseline"):
        assert after[field] == before[field]


def test_recording_first_sample_failure_is_explicit_successful_degradation(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path)

    def fail(**kwargs):
        raise RuntimeError("initial sample unavailable")

    service._sample_scenario = fail
    started = service.start_scenario_recording()
    assert started["active"] is True
    assert started["sample_count"] == 0
    assert started["initial_sample_error"] == "unavailable"
    assert service.stop_scenario_recording()["active"] is False


@pytest.mark.parametrize("sd_baseline", [False, True])
def test_pending_display_cleanup_cannot_stop_a_different_device(tmp_path, sd_baseline):
    module = _load_service_module()
    robot = _sd_baseline_robot() if sd_baseline else _procedural_robot()
    service = _service(module, tmp_path, robot)
    start = service.start_sd_baseline if sd_baseline else service.start_procedural
    status = service.sd_baseline_status if sd_baseline else service.procedural_status
    start()
    service._device_status_provider = lambda: {"online": False}
    service.maintain()
    robot.device_info = {"device_id": "different-device"}
    service._device_status_provider = lambda: {"online": True}
    before = robot.behavior.stop_calls if sd_baseline else list(robot.expression_runtime.calls)
    service.maintain()
    with pytest.raises(module.MediaLabBusyError, match="original device"):
        (service.stop_sd_baseline if sd_baseline else service.stop_procedural)()
    assert status()["state"] == "stop_required"
    assert status()["cleanup_device_id"] == "watcher-test"
    assert (robot.behavior.stop_calls if sd_baseline else robot.expression_runtime.calls) == before
    robot.device_info = {"device_id": "watcher-test"}
    service.maintain()
    assert status()["state"] == "idle"


@pytest.mark.parametrize("sd_baseline", [False, True])
def test_hot_device_switch_never_reuses_original_display_instance(tmp_path, sd_baseline):
    module = _load_service_module()
    robot = _sd_baseline_robot() if sd_baseline else _procedural_robot()
    service = _service(module, tmp_path, robot)
    start = service.start_sd_baseline if sd_baseline else service.start_procedural
    start()
    robot.device_info = {"device_id": "different-device"}
    with pytest.raises(module.MediaLabBusyError, match="original device"):
        start()
    status = service.sd_baseline_status() if sd_baseline else service.procedural_status()
    assert status["state"] == "stop_required"
    assert status["cleanup_device_id"] == "watcher-test"
    service.maintain()
    assert service.status()["resource_owners"] == {"animation": "sd_baseline" if sd_baseline else "procedural"}
    if sd_baseline:
        assert robot.behavior.stop_calls == 0 and len(robot.behavior.played) == 1
    else:
        assert robot.expression_runtime.calls == [True]


@pytest.mark.parametrize("sd_baseline", [False, True])
def test_daemon_device_identity_blocks_cleanup_even_if_sdk_identity_is_cached(tmp_path, sd_baseline):
    module = _load_service_module()
    robot = _sd_baseline_robot() if sd_baseline else _procedural_robot()
    service = _service(module, tmp_path, robot)
    (service.start_sd_baseline if sd_baseline else service.start_procedural)()
    service._device_status_provider = lambda: {"online": True, "device_id": "different-device"}
    with pytest.raises(module.MediaLabBusyError, match="original device"):
        (service.stop_sd_baseline if sd_baseline else service.stop_procedural)()
    if sd_baseline:
        assert robot.behavior.stop_calls == 0
    else:
        assert robot.expression_runtime.calls == [True]
    service._device_status_provider = lambda: {"online": True, "device_id": "watcher-test"}
    assert (service.stop_sd_baseline if sd_baseline else service.stop_procedural)()["state"] == "idle"


@pytest.mark.parametrize("sd_baseline", [False, True])
def test_display_start_refuses_stale_sdk_identity_from_another_device(tmp_path, sd_baseline):
    module = _load_service_module()
    robot = _sd_baseline_robot() if sd_baseline else _procedural_robot()
    service = _service(module, tmp_path, robot)
    service._device_status_provider = lambda: {"online": True, "device_id": "different-device"}
    with pytest.raises(module.MediaLabBusyError, match="original device"):
        (service.start_sd_baseline if sd_baseline else service.start_procedural)()
    assert not service.status()["resource_owners"]
    if sd_baseline:
        assert not robot.behavior.played
    else:
        assert not robot.expression_runtime.calls


@pytest.mark.parametrize("immediate", [False, True])
def test_final_sample_report_distinguishes_collection_from_rate_limit(tmp_path, monkeypatch, immediate):
    module = _load_service_module()
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    service = _service(module, tmp_path)
    service.start_scenario_recording()
    if not immediate:
        clock[0] += 1.1
    service.stop_scenario_recording()
    report = service.scenario_report()
    assert report["final_sample_status"] == ("skipped_rate_limit" if immediate else "sampled")
    assert report["sample_count"] == (1 if immediate else 2)


def test_procedural_status_uses_only_device_mouth_telemetry(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    robot.resource_snapshot["animation"] = {
        "audio_follow": True, "mouth_level_milli": 345, "pcm_frames": 42,
        "source": "rtc_playback", "design_id": 9,
    }
    service = _service(module, tmp_path, robot)
    service.start_procedural()
    state = service.status()["procedural"]
    assert state["telemetry_available"] is True
    assert state["mouth_level_milli"] == 345
    assert state["pcm_frames"] == 42
    assert state["mouth_source"] == "rtc_playback"


def test_scene_recording_is_bounded_and_exports_missing_telemetry_honestly(tmp_path, monkeypatch):
    module = _load_service_module()
    robot = _robot()
    robot.resource_snapshot = {}
    robot.resource_history = []
    service = _service(module, tmp_path, robot)
    current = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: current[0])
    monkeypatch.setattr(module, "_SCENARIO_MAX_SAMPLES", 2)
    client = _client_for_service(module, tmp_path, service)
    response = client.post("/api/scenario/recording/start", json={"label": "combined"})
    assert response.status_code == 200
    with pytest.raises(module.MediaLabBusyError):
        service.start_scenario_recording(label="again")
    for sequence in range(1, 4):
        current[0] += 1.1
        robot.resource_snapshot = {"sequence": sequence, "memory": {"internal": {"free_bytes": 200 - sequence}}}
        service.maintain()
    stopped = client.post("/api/scenario/recording/stop").json()
    assert stopped["active"] is False
    report = client.get("/api/scenario/report").json()
    assert report["sample_count"] == 4
    assert report["dropped_samples"] == 2
    assert len(report["samples"]) == 2
    assert report["samples"][0]["resources"]["sequence"] == 2
    assert report["summary"]["memory"]["internal"]["free_bytes_min"] == 197
    assert "dma" not in report["summary"]["memory"]
    assert report["samples"][0]["rtc"]["stats"] == {}


def test_scene_recording_marks_repeated_snapshot_stale(tmp_path, monkeypatch):
    module = _load_service_module()
    service = _service(module, tmp_path)
    current = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: current[0])
    service.start_scenario_recording()
    current[0] += 6.0
    service.maintain()
    report = service.scenario_report()
    assert report["samples"][-1]["telemetry"]["status"] == "stale"
    assert report["samples"][-1]["telemetry"]["age_seconds"] == 6.0


def test_procedural_telemetry_is_not_live_offline_or_after_reconnect_without_new_frame(tmp_path, monkeypatch):
    module = _load_service_module()
    robot = _procedural_robot()
    robot.resource_snapshot["animation"] = {
        "audio_follow": True, "mouth_level_milli": 345, "pcm_frames": 42,
        "source": "rtc_playback", "design_id": 9,
    }
    robot.resource_snapshot_received_at = 100.0
    current = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: current[0])
    connection = {"online": True, "request_id": "first"}
    service = _service(module, tmp_path, robot)
    service._device_status_provider = lambda: connection
    assert service.status()["procedural"]["telemetry_available"] is True
    current[0] = 106.0
    status = service.status()
    assert status["resources"]["telemetry"]["status"] == "stale"
    assert status["procedural"]["mouth_level_milli"] is None
    connection["online"] = False
    assert service.status()["resources"]["telemetry"]["status"] == "unavailable"
    connection.update(online=True, request_id="second")
    assert service.status()["resources"]["telemetry"]["status"] == "unavailable"
    robot.resource_snapshot_received_at = 106.0
    assert service.status()["resources"]["telemetry"]["status"] == "available"
    assert service.status()["procedural"]["mouth_level_milli"] == 345


def test_recording_freezes_initial_identity_and_excludes_cached_other_device_minima(tmp_path, monkeypatch):
    module = _load_service_module()
    robot = _robot()
    current = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: current[0])
    service = _service(module, tmp_path, robot)
    service.start_scenario_recording()
    initial_baseline = service._robot.resource_baseline
    current[0] += 1.1
    robot.device_info = {"device_id": "another-device", "firmware_version": "new"}
    robot.resource_baseline = {"sequence": 99}
    service.maintain()
    report = service.scenario_report()
    assert report["device"]["device_id"] == "watcher-test"
    assert report["baseline"] == initial_baseline
    assert report["samples"][-1]["device_id"] == "another-device"
    assert report["samples"][-1]["telemetry"]["status"] == "unavailable"
    assert report["samples"][-1]["connection"]["request_id"] is None


def _service(
    module: ModuleType,
    tmp_path: Path,
    robot: object | None = None,
    *,
    online: bool = True,
    device_pairer=None,
    rtc: object | None = None,
):
    sample_audio = tmp_path / "sample.wav"
    sample_audio.write_bytes(b"RIFF-test-audio")
    return module.MediaLabService(
        robot=robot or _robot(),
        rtc=rtc or FakeRtc(),
        artifacts_dir=tmp_path / "artifacts",
        sample_audio=sample_audio,
        device_status_provider=lambda: {
            "online": online,
            "state": "connected" if online else "idle",
            "last_error": None,
        },
        device_pairer=device_pairer or (
            lambda pairing_code, device_ip=None: {
                "device": {
                    "online": False,
                    "state": "discovering",
                    "request_id": "pairing-request",
                    "last_error": None,
                }
            }
        ),
    )


def _client_for_service(module: ModuleType, tmp_path: Path, service: object) -> TestClient:
    web_root = tmp_path / "control-web"
    web_root.mkdir(exist_ok=True)
    for filename in (
        "index.html",
        "app.js",
        "styles.css",
        "rtc-audio-health.mjs",
        "resource-health.mjs",
    ):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    return TestClient(module.create_web_app(service, web_root=web_root))


def test_rtc_diagnostic_speech_serves_only_the_bundled_fixture(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    client = _client_for_service(module, tmp_path, service)
    response = client.get("/api/diagnostics/rtc-speech")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFF-test-audio"
    assert client.get("/api/diagnostics/rtc-speech/other.wav").status_code == 404
    (tmp_path / "sample.wav").unlink()
    assert client.get("/api/diagnostics/rtc-speech").status_code == 404


def test_status_exposes_device_capabilities_and_idle_operation(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)

    status = service.status()

    assert status["connected"] is True
    assert status["busy"] is False
    assert status["active_action"] is None
    assert status["active_actions"] == []
    assert status["resource_owners"] == {}
    assert status["capabilities"] == [
        "motion",
        "light",
        "animation",
        "animation.prefetch.v1",
        "audio.stream",
        "microphone",
        "camera.capture",
        "camera.capture.feedback.v1",
        "rtc.audio.full_duplex.v1",
        "rtc.video.mjpeg.v1",
    ]
    assert status["device"]["firmware_version"] == "V3.1"
    assert status["artifacts"] == {}


def test_offline_device_is_reported_and_hardware_actions_fail_closed(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    service = _service(module, tmp_path, robot, online=False)

    status = service.status()

    assert status["connected"] is False
    assert status["connection"] == {
        "online": False,
        "state": "idle",
        "last_error": None,
    }
    with pytest.raises(module.MediaLabDeviceOfflineError, match="offline"):
        service.play_audio()
    assert robot.audio.paths == []


def test_daemon_pairing_request_targets_python_sdk(monkeypatch) -> None:
    module = _load_service_module()
    captured_payloads: list[dict[str, object]] = []

    class PairingResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return b'{"device":{"state":"discovering","online":false}}'

    def fake_urlopen(request, *, timeout):
        assert timeout == 0.5
        captured_payloads.append(json.loads(request.data.decode("utf-8")))
        return PairingResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    provider = module.DaemonDeviceStatusProvider(
        "http://127.0.0.1:8767/daemon/devices"
    )

    provider.pair("123456", "192.168.1.157")

    assert captured_payloads == [
        {
            "pairing_code": "123456",
            "target_mode": "python_sdk",
            "device_ip": "192.168.1.157",
        }
    ]


def test_online_transition_refreshes_the_sdk_device_snapshot(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    robot.capabilities = ()
    robot.device_info = {}
    refresh_calls: list[float] = []

    def refresh_device_info(*, timeout: float) -> dict[str, str]:
        refresh_calls.append(timeout)
        robot.capabilities = ("audio.stream", "camera.capture", "microphone")
        robot.device_info = {
            "device_id": "watcher-reconnected",
            "firmware_version": "V3.1",
        }
        return robot.device_info

    robot.refresh_device_info = refresh_device_info
    service = _service(module, tmp_path, robot, online=True)

    service.maintain()
    status = service.status()

    assert refresh_calls == [1.0]
    assert status["connected"] is True
    assert status["capabilities"] == [
        "audio.stream",
        "camera.capture",
        "microphone",
    ]
    assert status["device"]["device_id"] == "watcher-reconnected"


def test_http_app_returns_stable_device_offline_error(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path, online=False)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    response = client.post("/api/actions/play-audio")

    assert response.status_code == 409
    assert response.json() == {
        "error": "device_offline",
        "message": "Watcher device is offline",
    }


def test_http_app_accepts_six_digit_pairing_code_without_logging_it(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    pair_requests: list[tuple[str, str | None]] = []

    def pair_device(
        pairing_code: str,
        device_ip: str | None = None,
    ) -> dict[str, object]:
        pair_requests.append((pairing_code, device_ip))
        return {
            "device": {
                "online": False,
                "state": "discovering",
                "request_id": "pairing-request",
                "last_error": None,
            }
        }

    service = _service(
        module,
        tmp_path,
        online=False,
        device_pairer=pair_device,
    )
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    response = client.post(
        "/api/device/pair",
        json={"pairing_code": "123456", "device_ip": "192.168.1.157"},
    )

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "connection": {
            "online": False,
            "state": "discovering",
            "request_id": "pairing-request",
            "last_error": None,
        },
    }
    assert pair_requests == [("123456", "192.168.1.157")]
    assert "123456" not in str(service.events())


@pytest.mark.parametrize("pairing_code", ["", "12345", "1234567", "12A456"])
def test_http_app_rejects_invalid_pairing_codes(
    tmp_path: Path,
    pairing_code: str,
) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path, online=False)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    response = client.post(
        "/api/device/pair",
        json={"pairing_code": pairing_code},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_capture_photo_persists_only_the_managed_jpeg_artifact(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    service = _service(module, tmp_path, robot)

    result = service.capture_photo()

    photo = tmp_path / "artifacts" / "camera.jpg"
    assert photo.read_bytes() == b"\xff\xd8media-lab\xff\xd9"
    assert result["artifact"] == "camera.jpg"
    assert result["bytes"] == photo.stat().st_size
    assert result["content_type"] == "image/jpeg"
    assert robot.camera.calls == [
        {"width": 0, "height": 0, "quality": 0, "timeout": 10.0}
    ]


def test_capture_photo_with_feedback_uses_distinct_sdk_path(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    service = _service(module, tmp_path, robot)

    result = service.capture_photo_with_feedback()

    photo = tmp_path / "artifacts" / "camera-feedback.jpg"
    assert photo.read_bytes() == b"\xff\xd8media-lab-feedback\xff\xd9"
    assert result["artifact"] == "camera-feedback.jpg"
    assert result["bytes"] == photo.stat().st_size
    assert result["content_type"] == "image/jpeg"
    assert robot.camera.feedback_calls == [
        {"width": 0, "height": 0, "quality": 0, "timeout": 10.0}
    ]


def test_capture_photo_with_feedback_requires_firmware_capability(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    robot.capabilities = tuple(
        capability
        for capability in robot.capabilities
        if capability != "camera.capture.feedback.v1"
    )
    service = _service(module, tmp_path, robot)

    with pytest.raises(Exception, match="camera.capture.feedback.v1"):
        service.capture_photo_with_feedback()


@pytest.mark.parametrize("resource", ["microphone", "speaker"])
def test_feedback_capture_rejects_existing_audio_owner(tmp_path, resource):
    module = _load_service_module()
    robot = _robot()
    service = _service(module, tmp_path, robot)
    with service._operation("audio_owner", resource=resource):
        with pytest.raises(module.MediaLabBusyError, match="audio_owner"):
            service.capture_photo_with_feedback()
        assert robot.camera.feedback_calls == []
        assert service.status()["resource_owners"] == {resource: "audio_owner"}
        assert service.capture_photo()["bytes"] > 0
    assert service.status()["resource_owners"] == {}


@pytest.mark.parametrize("capture_fails", [False, True])
def test_feedback_capture_reserves_audio_until_completion(tmp_path, capture_fails):
    module = _load_service_module()
    robot = _robot()
    service = _service(module, tmp_path, robot)

    def capture(**kwargs):
        assert set(service.status()["resource_owners"]) == {
            "camera", "animation", "microphone", "speaker",
        }
        with pytest.raises(module.MediaLabBusyError):
            service.play_audio()
        with pytest.raises(module.MediaLabBusyError):
            service.record_microphone(duration=1.0)
        if capture_fails:
            raise TimeoutError("camera unavailable")
        return SimpleNamespace(data=b"jpeg")

    robot.camera.capture_with_feedback = capture
    if capture_fails:
        with pytest.raises(TimeoutError, match="camera unavailable"):
            service.capture_photo_with_feedback()
    else:
        service.capture_photo_with_feedback()
    assert service.status()["resource_owners"] == {}
    assert all(not lock.locked() for lock in service._resource_locks.values())


def test_record_microphone_writes_valid_pcm_wave_and_metrics(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    service = _service(module, tmp_path, robot)

    result = service.record_microphone(duration=1.25)

    recording = tmp_path / "artifacts" / "microphone.wav"
    with wave.open(str(recording), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() == 20000
    assert result == {
        "artifact": "microphone.wav",
        "bytes": recording.stat().st_size,
        "content_type": "audio/wav",
        "duration_seconds": 1.25,
        "dropped_frames": 0,
        "decode_failures": 0,
    }
    assert robot.microphone.calls == [
        {"duration": 1.25, "timeout": 3.25, "queue_size": 32}
    ]


def test_motion_control_uses_public_sdk_domain_and_waits_for_completion(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    client = _client_for_service(module, tmp_path, _service(module, tmp_path, robot))

    response = client.post(
        "/api/controls/motion/move",
        json={"pan_deg": 40, "tilt_deg": 115, "duration_ms": 600},
    )


    assert response.status_code == 200
    assert response.json() == {
        "completed": True,
        "operation_id": 101,
        "pan_deg": 40,
        "tilt_deg": 115,
    }
    assert robot.motion.moves == [
        {
            "pan_deg": 40,
            "tilt_deg": 115,
            "duration_ms": 600,
            "profile": "ease_in_out",
        }
    ]
    assert robot.motion.job.wait_calls == [2.6]


@pytest.mark.parametrize(
    "payload",
    [
        {"pan_deg": 29, "tilt_deg": 115, "duration_ms": 600},
        {"pan_deg": 151, "tilt_deg": 115, "duration_ms": 600},
        {"pan_deg": 90, "tilt_deg": 99, "duration_ms": 600},
        {"pan_deg": 90, "tilt_deg": 131, "duration_ms": 600},
    ],
)
def test_motion_control_rejects_targets_outside_physical_limits(
    tmp_path: Path, payload: dict[str, int]
) -> None:
    module = _load_service_module()
    client = _client_for_service(module, tmp_path, _service(module, tmp_path, _robot()))

    response = client.post("/api/controls/motion/move", json=payload)

    assert response.status_code == 422


def test_light_controls_use_public_sdk_domain(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    client = _client_for_service(module, tmp_path, _service(module, tmp_path, robot))

    color = client.post(
        "/api/controls/lights/color",
        json={"color": "#4da3ff", "brightness": 0.7, "zone": "side"},
    )
    effect = client.post(
        "/api/controls/lights/effect",
        json={
            "effect": "breathing",
            "color": "#D9FF57",
            "brightness": 0.5,
            "zone": "all",
            "period_ms": 800,
        },
    )
    off = client.post("/api/controls/lights/off")

    assert color.json() == {"applied": True}
    assert effect.json() == {"started": True, "operation_id": 202}
    assert off.json() == {"off": True}
    assert robot.lights.colors == [("#4da3ff", 0.7, "side")]
    assert robot.lights.effects == [
        {
            "effect": "breathing",
            "color": "#D9FF57",
            "brightness": 0.5,
            "zone": "all",
            "period_ms": 800,
            "repeat": 0,
        }
    ]
    assert robot.lights.off_calls == 1


def test_animation_control_uses_public_sdk_domain_and_accepts_catalog_ids(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    client = _client_for_service(module, tmp_path, _service(module, tmp_path, robot))

    played = client.post(
        "/api/controls/animation/play",
        json={"animation_id": "standby_little4"},
    )
    prefetched = client.post(
        "/api/controls/animation/prefetch",
        json={"animation_id": "happy"},
    )
    stopped = client.post("/api/controls/animation/stop")

    assert played.status_code == 200
    assert prefetched.json() == {"prefetched": True, "animation_id": "happy"}
    assert played.json() == {
        "started": True,
        "operation_id": 303,
        "animation_id": "standby_little4",
    }
    assert stopped.json() == {"stopped": True}
    assert robot.animation.played == ["standby_little4"]
    assert robot.animation.prefetched == ["happy"]
    assert robot.animation.stop_calls == 1


@pytest.mark.parametrize("animation_id", ["", "UPPER", "../bad", "bad-id", "x" * 64])
def test_animation_control_rejects_unsafe_resource_ids(
    tmp_path: Path,
    animation_id: str,
) -> None:
    module = _load_service_module()
    client = _client_for_service(module, tmp_path, _service(module, tmp_path))

    response = client.post(
        "/api/controls/animation/play",
        json={"animation_id": animation_id},
    )

    assert response.status_code == 422


def test_animation_ui_uses_the_device_catalog_and_supports_prefetched_shuffle_bag_playback() -> None:
    web_root = Path(__file__).parents[1] / "examples" / "sdk_media_lab" / "web"
    document = web_root.joinpath("index.html").read_text(encoding="utf-8")
    javascript = web_root.joinpath("app.js").read_text(encoding="utf-8")

    assert 'id="animationSuggestions"></datalist>' in document
    assert "standby_blink" not in document
    assert "emotion_happy" not in document
    assert 'id="startRandomAnimationButton"' in document
    assert 'id="stopRandomAnimationButton"' in document
    assert "renderAnimationCatalog(status.animations, status.connected)" in javascript
    assert 'api("/api/controls/animation/prefetch"' in javascript
    assert "createAnimationShuffleBag," in javascript
    assert "remainingIds: []" in javascript
    assert "randomState.remainingIds = createAnimationShuffleBag(" in javascript
    assert "const nextId = randomState.remainingIds[0] || null;" in javascript


def test_rtc_media_lease_allows_motion_lights_and_animation(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    service = _service(module, tmp_path, robot)

    service.start_live_video(mode="av")

    assert service.move_motion(pan_deg=90, tilt_deg=115, duration_ms=600)["completed"] is True
    assert service.set_light_color(color="#D9FF57", brightness=0.7, zone="all") == {"applied": True}
    assert service.play_animation(animation_id="thinking")["started"] is True
    assert service.status()["resource_owners"] == {
        "camera": "rtc_av",
        "microphone": "rtc_av",
        "speaker": "rtc_av",
    }


def test_audio_rtc_allows_photo_but_rejects_standalone_audio_actions(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    service.start_live_video(mode="audio")

    assert service.capture_photo()["content_type"] == "image/jpeg"
    with pytest.raises(module.MediaLabBusyError, match="rtc_audio"):
        service.play_audio()
    with pytest.raises(module.MediaLabBusyError, match="rtc_audio"):
        service.record_microphone(duration=1.0)


def test_video_rtc_allows_one_standalone_audio_direction_at_a_time(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    service.start_live_video(mode="video")

    assert service.play_audio()["bytes"] > 0
    assert service.record_microphone(duration=0.1)["content_type"] == "audio/wav"
    with pytest.raises(module.MediaLabBusyError, match="live_video"):
        service.capture_photo()


@pytest.mark.parametrize("action", ["play_audio", "capture_photo", "record_microphone"])
def test_combined_rtc_rejects_every_standalone_media_action(tmp_path: Path, action: str) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    service.start_live_video(mode="av")

    with pytest.raises(module.MediaLabBusyError, match="rtc_av"):
        if action == "record_microphone":
            service.record_microphone(duration=1.0)
        else:
            getattr(service, action)()


def test_resource_locks_only_serialize_actions_that_share_the_same_hardware(tmp_path: Path) -> None:
    module = _load_service_module()
    release = threading.Event()
    started = threading.Event()

    class BlockingPlayback(FakePlayback):
        def wait(self, timeout: float) -> None:
            started.set()
            super().wait(timeout)

    service = _service(module, tmp_path, _robot(playback=BlockingPlayback(release)))
    thread = threading.Thread(target=service.play_audio, daemon=True)
    thread.start()
    assert started.wait(timeout=1.0)

    assert service.move_motion(pan_deg=90, tilt_deg=115, duration_ms=600)["completed"] is True
    assert service.play_animation(animation_id="thinking")["started"] is True
    assert service.capture_photo()["content_type"] == "image/jpeg"

    release.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()


@pytest.mark.parametrize(
    ("path", "capability"),
    [
        ("/api/controls/motion/move", "motion"),
        ("/api/controls/lights/color", "light"),
    ],
)
def test_control_http_contract_rejects_missing_firmware_capability(
    tmp_path: Path,
    path: str,
    capability: str,
) -> None:
    module = _load_service_module()
    robot = _robot()
    robot.capabilities = tuple(item for item in robot.capabilities if item != capability)
    client = _client_for_service(module, tmp_path, _service(module, tmp_path, robot))
    payload = (
        {"pan_deg": 90, "tilt_deg": 115, "duration_ms": 600}
        if capability == "motion"
        else {"color": "#D9FF57", "brightness": 0.7, "zone": "all"}
    )

    response = client.post(path, json=payload)

    assert response.status_code == 409
    assert response.json() == {
        "error": "capability_unavailable",
        "message": f"Robot firmware does not advertise required capability: {capability}",
        "capability": capability,
    }


@pytest.mark.parametrize("duration", [0, -1, 30.1, float("inf"), float("nan")])
def test_record_microphone_rejects_unsafe_durations(
    tmp_path: Path,
    duration: float,
) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)

    with pytest.raises(ValueError, match="duration"):
        service.record_microphone(duration=duration)


def test_ordinary_audio_directions_are_serialized_while_independent_camera_remains_available(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    release = threading.Event()
    started = threading.Event()

    class BlockingPlayback(FakePlayback):
        def wait(self, timeout: float) -> None:
            started.set()
            super().wait(timeout)

    service = _service(module, tmp_path, _robot(playback=BlockingPlayback(release)))
    thread = threading.Thread(target=service.play_audio, daemon=True)
    thread.start()
    assert started.wait(timeout=1.0)

    assert service.status()["active_action"] == "play_audio"
    assert service.status()["resource_owners"] == {
        "microphone": "play_audio",
        "speaker": "play_audio",
    }
    with pytest.raises(module.MediaLabBusyError, match="play_audio"):
        service.play_audio()
    with pytest.raises(module.MediaLabBusyError, match="play_audio"):
        service.record_microphone(duration=1.0)
    assert service.capture_photo()["bytes"] > 0

    release.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert service.status()["busy"] is False


def test_http_app_serves_local_ui_actions_and_artifacts(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    web_root = tmp_path / "web"
    web_root.mkdir()
    web_root.joinpath("index.html").write_text(
        "<h1>SDK MEDIA LAB</h1>",
        encoding="utf-8",
    )
    web_root.joinpath("app.js").write_text("", encoding="utf-8")
    web_root.joinpath("styles.css").write_text("", encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    assert client.get("/").text == "<h1>SDK MEDIA LAB</h1>"
    assert client.get("/api/status").json()["connected"] is True

    response = client.post("/api/actions/capture-photo")
    assert response.status_code == 200
    assert response.json()["artifact_url"].startswith("/artifacts/camera.jpg?v=")
    assert client.get("/artifacts/camera.jpg").content.startswith(b"\xff\xd8")
    assert client.get("/artifacts/unknown.jpg").status_code == 404
    assert client.get("/artifacts/../app.py").status_code == 404


def test_http_app_serves_browser_health_modules(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    web_root = tmp_path / "web"
    web_root.mkdir()
    web_root.joinpath("index.html").write_text("lab", encoding="utf-8")
    web_root.joinpath("app.js").write_text("", encoding="utf-8")
    web_root.joinpath("styles.css").write_text("", encoding="utf-8")
    web_root.joinpath("rtc-audio-health.mjs").write_text(
        "export const ready = true;",
        encoding="utf-8",
    )
    web_root.joinpath("resource-health.mjs").write_text(
        "export const resourceReady = true;",
        encoding="utf-8",
    )
    web_root.joinpath("video-feedback.mjs").write_text(
        "export const feedbackReady = true;",
        encoding="utf-8",
    )
    web_root.joinpath("video-frame-queue.mjs").write_text(
        "export const frameQueueReady = true;",
        encoding="utf-8",
    )
    client = TestClient(module.create_web_app(service, web_root=web_root))

    response = client.get("/assets/rtc-audio-health.mjs")

    assert response.status_code == 200
    assert "export const ready" in response.text
    resource_response = client.get("/assets/resource-health.mjs")
    assert resource_response.status_code == 200
    assert "export const resourceReady" in resource_response.text
    feedback_response = client.get("/assets/video-feedback.mjs")
    assert feedback_response.status_code == 200
    assert "export const feedbackReady" in feedback_response.text
    frame_queue_response = client.get("/assets/video-frame-queue.mjs")
    assert frame_queue_response.status_code == 200
    assert "export const frameQueueReady" in frame_queue_response.text
    assert client.get("/assets/unknown.mjs").status_code == 404


def test_http_app_serves_every_module_imported_by_browser_entrypoint(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    web_root = LAB_ROOT / "web"
    client = TestClient(module.create_web_app(service, web_root=web_root))
    javascript = web_root.joinpath("app.js").read_text(encoding="utf-8")
    imported_modules = re.findall(r'from "\./([^"/]+\.mjs)"', javascript)

    assert imported_modules
    for imported_module in imported_modules:
        response = client.get(f"/assets/{imported_module}")
        assert response.status_code == 200, imported_module


def test_animation_confirmation_accepts_realtime_diagnostics_when_resource_sampling_is_busy() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")

    assert "status.rtc?.stats?.animation_active === true" in javascript
    assert "if (!state.animation.requestAccepted) return;" in javascript
    assert "state.animation.requestAccepted = true;" in javascript


def test_audio_latency_diagnostics_expose_the_browser_minimum_buffer() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")

    assert "browserLatency.minimumMs" in javascript
    assert "minimum ${browserLatency.minimumMs} ms" in javascript


def test_live_video_http_contract_forwards_browser_signaling_and_heartbeat(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    rtc = FakeRtc()
    service = _service(module, tmp_path, rtc=rtc)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    started = client.post("/api/video/session/start", json={"mode": "video"})
    offered = client.post(
        "/api/video/session/signal",
        json={"kind": "offer", "sdp": "v=0\r\n"},
    )
    candidate = client.post(
        "/api/video/session/signal",
        json={
            "kind": "candidate",
            "candidate": "candidate:1 1 UDP 1 192.168.1.2 1234 typ host",
            "sdp_mid": "0",
            "sdp_mline_index": 0,
        },
    )
    heartbeat = client.post(
        "/api/video/session/clock-ping",
        json={"browser_send_us": 123456},
    )
    events = client.get("/api/video/session/events?after=0")
    stopped = client.post("/api/video/session/stop")

    assert started.status_code == 200
    assert started.json()["session"]["state"] == "starting"
    assert offered.status_code == candidate.status_code == heartbeat.status_code == 200
    assert events.json()["events"][0]["message"]["type"] == "evt.rtc.signal"
    assert stopped.json() == {"stopped": True}
    assert rtc.calls == [
        ("start", "video"),
        ("offer", "v=0\r\n"),
        (
            "candidate",
            ("candidate:1 1 UDP 1 192.168.1.2 1234 typ host", "0", 0),
        ),
        ("clock_ping", 123456),
        ("stop", None),
    ]


def test_full_duplex_audio_http_contract_starts_audio_rtc_session(tmp_path: Path) -> None:
    module = _load_service_module()
    rtc = FakeRtc()
    service = _service(module, tmp_path, rtc=rtc)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    started = client.post("/api/rtc/session/start", json={"mode": "audio"})
    stopped = client.post("/api/rtc/session/stop")

    assert started.status_code == 200
    assert started.json()["session"]["mode"] == "audio"
    assert stopped.json() == {"stopped": True}
    assert rtc.calls == [("start", "audio"), ("stop", None)]


def test_combined_rtc_http_contract_uses_one_audio_video_session(tmp_path: Path) -> None:
    module = _load_service_module()
    rtc = FakeRtc()
    service = _service(module, tmp_path, rtc=rtc)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    started = client.post("/api/rtc/session/start", json={"mode": "av"})
    status = client.get("/api/status")
    stopped = client.post("/api/rtc/session/stop")

    assert started.status_code == 200
    assert started.json()["session"]["mode"] == "av"
    assert status.json()["resource_owners"] == {
        "camera": "rtc_av",
        "microphone": "rtc_av",
        "speaker": "rtc_av",
    }
    assert stopped.json() == {"stopped": True}
    assert rtc.calls == [("start", "av"), ("stop", None)]


def test_media_lab_keeps_audio_capability_compatible_with_older_sdk_runtime() -> None:
    module = _load_service_module()

    assert module.RTC_AUDIO_CAPABILITY == "rtc.audio.full_duplex.v1"


def test_full_duplex_audio_requires_explicit_firmware_capability(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    robot.capabilities = ("rtc.video.mjpeg.v1",)
    service = _service(module, tmp_path, robot)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    response = client.post("/api/rtc/session/start", json={"mode": "audio"})

    assert response.status_code == 409
    assert response.json()["error"] == "rtc_unavailable"


def test_combined_rtc_mode_requires_both_audio_and_video_capabilities(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    robot.capabilities = ("rtc.video.mjpeg.v1",)
    service = _service(module, tmp_path, robot)

    with pytest.raises(module.MediaLabRtcError, match="rtc.audio.full_duplex.v1"):
        service.start_live_video(mode="av")


def test_rtc_start_rejection_exposes_busy_owner_and_releases_media_lease(tmp_path: Path) -> None:
    module = _load_service_module()

    class RejectingRtc(FakeRtc):
        def start(self, *, mode: str = "video") -> dict[str, object]:
            self.calls.append(("start", mode))
            raise module.application_rtc.RtcSessionRejectedError(
                "ctrl.rtc.session.start",
                "busy",
                owner="audio_playback",
            )

    rtc = RejectingRtc()
    service = _service(module, tmp_path, rtc=rtc)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    response = client.post("/api/video/session/start", json={"mode": "video"})

    assert response.status_code == 409
    assert response.json() == {
        "error": "rtc_resource_busy",
        "message": "RTC media resource is busy: audio_playback",
        "owner": "audio_playback",
    }
    assert service.status()["resource_owners"] == {}
    assert rtc.calls == [("start", "video")]


def test_live_video_requires_explicit_firmware_capability(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = _robot()
    robot.capabilities = ("camera.capture",)
    service = _service(module, tmp_path, robot)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    response = client.post("/api/video/session/start", json={"mode": "video"})

    assert response.status_code == 409
    assert response.json()["error"] == "rtc_unavailable"


def test_live_video_stop_failure_keeps_camera_exclusive_but_not_speaker(tmp_path: Path) -> None:
    module = _load_service_module()
    rtc = FakeRtc()
    service = _service(module, tmp_path, rtc=rtc)
    service.start_live_video()

    def fail_stop() -> bool:
        raise ConnectionError("device channel unavailable")

    rtc.stop = fail_stop  # type: ignore[method-assign]

    with pytest.raises(ConnectionError, match="device channel unavailable"):
        service.stop_live_video()

    with pytest.raises(module.MediaLabBusyError, match="live_video"):
        service.capture_photo()
    assert service.play_audio()["bytes"] > 0


def test_rtc_failure_event_keeps_camera_exclusive_until_device_reports_stopped(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    rtc = FakeRtc()
    service = _service(module, tmp_path, rtc=rtc)
    service.start_live_video()

    rtc._state.update(  # noqa: SLF001 - simulate the Device's failure-before-stop sequence
        active=True,
        state="failed",
        last_error="mjpeg_data_channel_closed",
    )
    service.maintain()

    assert service.status()["resource_owners"] == {"camera": "live_video"}
    with pytest.raises(module.MediaLabBusyError, match="live_video"):
        service.capture_photo()
    assert service.play_audio()["bytes"] > 0

    rtc._state.update(active=False, state="stopped")  # noqa: SLF001
    service.maintain()

    assert service.status()["resource_owners"] == {}


def test_live_video_offline_cleanup_resets_rtc_before_releasing_media_lock(tmp_path: Path) -> None:
    module = _load_service_module()
    rtc = FakeRtc()
    online = True
    service = _service(module, tmp_path, rtc=rtc)
    service.start_live_video()
    service._device_status_provider = lambda: {  # noqa: SLF001 - simulate a disconnect
        "online": online,
        "state": "connected" if online else "idle",
        "last_error": None,
    }

    online = False
    status_before_maintenance = service.status()

    assert ("reset", "device_offline") not in rtc.calls
    assert status_before_maintenance["active_action"] == "live_video"
    assert status_before_maintenance["rtc"]["active"] is True

    service.maintain()
    status = service.status()

    assert ("reset", "device_offline") in rtc.calls
    assert status["active_action"] is None
    assert status["rtc"]["active"] is False

    online = True
    restarted = service.start_live_video()
    assert restarted["session"]["active"] is True


def test_maintenance_does_not_release_camera_lock_while_live_video_is_starting(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    rtc = FakeRtc()
    rtc._state.update(active=False, state="stopped")
    service = _service(module, tmp_path, rtc=rtc)
    start_entered = threading.Event()
    allow_start = threading.Event()
    original_ensure_online = service._ensure_device_online  # noqa: SLF001 - race harness

    def block_before_rtc_start() -> None:
        original_ensure_online()
        start_entered.set()
        assert allow_start.wait(timeout=1.0)

    service._ensure_device_online = block_before_rtc_start  # type: ignore[method-assign]  # noqa: SLF001
    start_thread = threading.Thread(target=service.start_live_video)
    start_thread.start()
    assert start_entered.wait(timeout=1.0)

    maintenance_thread = threading.Thread(target=service.maintain)
    maintenance_thread.start()
    maintenance_thread.join(timeout=0.05)
    assert maintenance_thread.is_alive()
    allow_start.set()
    start_thread.join(timeout=1.0)
    maintenance_thread.join(timeout=1.0)

    assert not start_thread.is_alive()
    assert not maintenance_thread.is_alive()
    with pytest.raises(module.MediaLabBusyError, match="live_video"):
        service.capture_photo()
    assert service.play_audio()["bytes"] > 0


def test_web_app_lifespan_runs_media_lab_maintenance(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    maintenance_started = threading.Event()
    original_maintain = service.maintain

    def tracked_maintain() -> None:
        original_maintain()
        maintenance_started.set()

    service.maintain = tracked_maintain  # type: ignore[method-assign]
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")

    with TestClient(module.create_web_app(service, web_root=web_root)):
        assert maintenance_started.wait(timeout=1.0)


def test_browser_counts_drop_only_when_pending_frame_is_replaced() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")

    assert 'from "./video-frame-queue.mjs"' in javascript
    assert "if (admission.replacedPending) state.rtc.droppedFrames += 1;" in javascript
    assert "finishVideoFrameDecode(state.rtc, admission.ownsDecoder)" in javascript
    assert "current = takePendingVideoFrame(state.rtc);" in javascript
    assert "bytes.subarray(headerSize)" in javascript
    assert "state.rtc.pendingFrame = frame;\n      state.rtc.droppedFrames += 1;" not in javascript


def test_media_lab_video_feedback_uses_recent_drop_window() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")

    assert 'from "./video-feedback.mjs"' in javascript
    assert "updateVideoCongestionFeedback(state.rtc.videoCongestionFeedback" in javascript
    assert "state.rtc.videoCongestionFeedback = videoCongestion" in javascript
    assert "previousDroppedFrames: state.rtc.feedbackDroppedFrames" in javascript
    assert "state.rtc.feedbackDroppedFrames = state.rtc.droppedFrames" in javascript
    assert "state.rtc.droppedFrames > 0 ? 1 : 0" not in javascript


def test_media_lab_video_ui_exposes_pipeline_throughput() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    document = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")

    for metric in ("source_fps_x100", "target_fps", "sent_fps_x100", "video_egress_p95_us"):
        assert metric in javascript
    assert 'id="liveVideoPipelineFps"' in document
    assert 'id="liveVideoTransport"' in document


def test_media_lab_video_ui_exposes_animation_and_congestion_pressure() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    document = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")

    for metric in (
        "browser_congestion_level",
        "animation_measured_fps_x100",
        "animation_target_fps_x100",
        "animation_recent_underruns",
        "animation_late_max_us",
        "animation_pressure_level",
    ):
        assert metric in javascript
    assert 'id="liveVideoCongestion"' in document
    assert 'id="liveVideoAnimation"' in document


def test_media_lab_ui_uses_resource_owners_instead_of_global_busy_for_controls() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    document = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")

    assert 'from "./media-resource-policy.mjs"' in javascript
    assert "status.resource_owners" in javascript
    assert "elements.applyMotionButton.disabled = unavailable" not in javascript
    assert "elements.applyLightButton.disabled = unavailable" not in javascript
    assert 'id="playAnimationButton"' in document
    assert 'id="animationId"' in document
    assert 'state.localResources.add("media")' in javascript
    assert 'state.localResources.delete("media")' in javascript
    assert 'navigator.sendBeacon(rtcEndpoint("stop", mode), new Blob' in javascript
    assert "status.rtc?.active === true" in javascript
    assert "state.status?.rtc?.active === true" in javascript


def test_media_lab_ui_can_start_one_combined_audio_video_rtc_session() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    document = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")

    assert 'id="startRtcAvButton"' in document
    assert 'startRtcSession("av")' in javascript
    assert "rtcModeHasAudio" in javascript
    assert "rtcModeHasVideo" in javascript
    assert "teardownInProgress" in javascript
    assert 'JSON.stringify({ mode, request_id: requestId })' in javascript
    assert "isCurrentRtcGeneration(state.rtc.generation, generation)" in javascript
    assert "pollRtcEvents(generation)" in javascript


def test_media_lab_stop_closes_browser_media_before_waiting_for_device_release() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    stop_body = javascript.split("async function stopRtcSession(", 1)[1].split(
        "async function failRtcSession", 1
    )[0]

    assert stop_body.index("cleanupRtcSession();") < stop_body.index(
        'await api(rtcEndpoint("stop", mode), { method: "POST", body: JSON.stringify({ request_id: requestId }) });'
    )
    assert "Local audio/video stopped, but device release confirmation timed out" in stop_body
    assert "elements.stopLiveVideoButton.disabled = !hadVideo || !state.rtc.peer" not in stop_body
    assert "elements.stopRtcAudioButton.disabled = !hadAudio || !state.rtc.peer" not in stop_body


def test_media_lab_keeps_rtc_start_controls_disabled_until_teardown_finishes() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    render_body = javascript.split("function renderStatus(status)", 1)[1].split(
        "function renderCapabilities", 1
    )[0]
    cleanup_body = javascript.split("function cleanupRtcSession()", 1)[1].split(
        "async function enqueueMjpegPacket", 1
    )[0]

    assert "!availability.startRtcVideo || !liveAvailable || state.rtc.teardownInProgress" in render_body
    assert "!availability.startRtcAudio || !rtcAudioAvailable || state.rtc.teardownInProgress" in render_body
    assert "!availability.startRtcAv || !liveAvailable || !rtcAudioAvailable\n    || state.rtc.teardownInProgress" in render_body
    assert "elements.startLiveVideoButton.disabled = state.rtc.teardownInProgress" in cleanup_body
    assert "elements.startRtcAudioButton.disabled = state.rtc.teardownInProgress" in cleanup_body
    assert "elements.startRtcAvButton.disabled = state.rtc.teardownInProgress" in cleanup_body


def test_media_lab_ui_explains_rtc_audio_playback_conflicts() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")

    assert "payload.error" in javascript
    assert 'code === "rtc_resource_busy"' in javascript
    assert "Speaker or animation audio is playing. Stop it before starting this feature" in javascript


def test_media_lab_browser_entrypoint_only_queries_declared_element_ids() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    document = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")

    queried_ids = set(re.findall(r'document\.querySelector\("#([^" ]+)"\)', javascript))
    declared_ids = set(re.findall(r'id="([^"]+)"', document))

    assert queried_ids
    assert queried_ids <= declared_ids


def test_browser_declares_shared_formatters_only_once() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")

    assert javascript.count("function formatBytes(") == 1


def test_rtc_audio_ui_uses_raw_microphone_and_aec_diagnostics() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    document = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")

    assert "audio_microphone_peak" in javascript
    assert "audio_aec_active" in javascript
    assert "audio_aec_reference_bytes" in javascript
    assert "audio_aec_reference_processed_bytes" in javascript
    assert "reference processed" in javascript
    assert "rtcAudioAec" in javascript
    assert "id=\"rtcAudioAec\"" in document
    assert "Physical Mic Peak" in document


def test_browser_mdns_host_candidates_are_rewritten_without_touching_other_candidates() -> None:
    module = _load_service_module()
    offer = (
        "v=0\r\n"
        "a=candidate:1 1 udp 2113937151 browser-host.local 62768 typ host generation 0\r\n"
        "a=candidate:2 1 udp 1677734911 203.0.113.20 45678 typ srflx\r\n"
    )

    rewritten = module._rewrite_mdns_host_candidates(offer, "192.168.1.110")

    assert "browser-host.local" not in rewritten
    assert "192.168.1.110 62768 typ host" in rewritten
    assert "203.0.113.20 45678 typ srflx" in rewritten


def test_http_app_maps_validation_and_busy_failures_to_stable_errors(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    web_root = tmp_path / "web"
    web_root.mkdir()
    for filename in ("index.html", "app.js", "styles.css"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    client = TestClient(module.create_web_app(service, web_root=web_root))

    invalid = client.post(
        "/api/actions/record-microphone",
        json={"duration": 31},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"] == "invalid_request"

    service._resource_locks["speaker"].acquire()
    service._active_actions["speaker"] = "capture_photo"
    service._refresh_active_action_locked()
    try:
        busy = client.post("/api/actions/play-audio")
    finally:
        service._active_actions.pop("speaker")
        service._refresh_active_action_locked()
        service._resource_locks["speaker"].release()
    assert busy.status_code == 409
    assert busy.json() == {
        "error": "busy",
        "message": "media lab is busy with capture_photo",
    }


def test_local_ui_uses_english_source_copy_without_chinese_hardcoding() -> None:
    html = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    i18n = LAB_ROOT.joinpath("web", "i18n.mjs").read_text(encoding="utf-8")

    assert '<html lang="en" data-i18n-ready="false">' in html
    assert 'defaultLocale: "en-US"' in javascript
    for copy in (
        "SDK Test Bench",
        "Connect Robot",
        "Run Basic Check",
        "Gimbal Position",
        "Body Lighting",
        "Speaker Stream",
        "Resource Lifecycle",
        "Release Result",
        "Camera Capture",
        "Live Camera Preview",
        "Start Live Video",
        "Full-duplex Audio Call",
        "Start Full-duplex Call",
        "Microphone Recording",
        "Device Capability Matrix",
        "Run Log",
        "Awaiting Test",
    ):
        assert copy in html

    for copy in (
        "Device Online",
        "Pairing code must contain 6 digits",
        "Discovering device",
        "System Idle",
        "Streaming PCM sample",
        "rtc-control",
        "mjpeg_websocket_url",
        "createMjpegTransport",
        "parseWjpgPacket",
        'api("/api/video/session/start"',
        "navigator.mediaDevices.getUserMedia",
        "createRtcMicrophoneConstraints",
        "rtc_audio_processing",
        'window.location.hostname === "127.0.0.1"',
        'params.get("rtc_hil") === "1"',
        "createRtcTestSpeech",
        "diagnosticAudio.dispose()",
        "state.rtc.diagnosticAudio",
        "for (const track of localStream.getTracks()) track.stop();",
        "state.rtc.generation !== generation",
        'api("/api/rtc/session/start"',
        'addEventListener("track"',
        "Local audio/video stopped, but device release confirmation timed out",
        "Basic check passed",
    ):
        assert copy in javascript

    for english_copy in (
        "SDK Test Bench",
        "Connect Robot",
        "Run Basic Check",
        "Speaker Stream",
        "Camera Capture",
        "Microphone Recording",
        "Awaiting Test",
        "System Idle",
    ):
        assert english_copy in html + i18n

    assert not re.search(r"[\u4e00-\u9fff]", html + javascript)

    assert "localResources: new Set()" in javascript
    assert "actionResources.some((name) => state.localResources.has(name))" in javascript
    assert "const ownsResource = !interrupt;" in javascript
    assert 'resource: "motion"' in javascript
    assert 'resource: "light"' in javascript
    assert 'resource: "animation"' in javascript
    assert 'resources: ["microphone", "speaker"]' in javascript
    assert "resources: controls.photoResources" in javascript
    assert javascript.count('resources: ["microphone", "speaker"]') == 2
    assert 'path: "/api/actions/stop-audio"' in javascript
    assert 'path: "/api/controls/motion/stop"' in javascript
    assert javascript.count("interrupt: true") >= 2
    assert "Device disconnected. Reconnect before testing" in javascript
    assert 'api("/api/device/pair"' in javascript
    assert 'autocomplete="one-time-code"' in html
    assert 'name="device_ip"' in html
    assert "device_ip: deviceIp || null" in javascript


def test_media_lab_csp_allows_direct_device_websocket(tmp_path: Path) -> None:
    module = _load_service_module()
    client = TestClient(
        module.create_web_app(_service(module, tmp_path), web_root=LAB_ROOT / "web")
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "connect-src 'self' ws:" in response.headers["content-security-policy"]
    assert "'wasm-unsafe-eval'" in response.headers["content-security-policy"]
    assert "'unsafe-eval'" not in response.headers["content-security-policy"]


def test_local_rtc_diagnostic_upload_is_bounded_and_names_are_scoped(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)
    client = TestClient(module.create_web_app(service, web_root=LAB_ROOT / "web"))
    recording_id = "00000000-0000-4000-8000-000000000001"
    client.headers["X-Recording-Id"] = recording_id
    body = b"\x1a\x45\xdf\xa3" + b"fixture"
    assert client.post("/api/diagnostics/rtc-audio/robot-raw", content=body,
                       headers={"Content-Type": "audio/webm"}).status_code == 200
    assert client.get(f"/artifacts/rtc-diagnostic-{recording_id}-robot-raw.webm").content == body
    assert client.post("/api/diagnostics/rtc-audio/unknown", content=body).status_code == 404
    assert client.post("/api/diagnostics/rtc-audio/computer", content=b"invalid").status_code == 400
    assert client.post("/api/diagnostics/rtc-audio/computer", content=body + bytes(2 * 1024 * 1024)).status_code == 413
    assert client.post("/api/diagnostics/rtc-audio/report", json={"recordingId": recording_id, "samples": []}).status_code == 409


def test_partial_and_parallel_diagnostic_batches_do_not_mix_or_replace_complete_results(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    module = _load_service_module()
    service = _service(module, tmp_path)
    client = TestClient(module.create_web_app(service, web_root=LAB_ROOT / "web"))

    def upload(batch, channel):
        headers = {"X-Recording-Id": batch}
        if channel == "report":
            return client.post("/api/diagnostics/rtc-audio/report", headers=headers,
                               json={"recordingId": batch, "samples": []})
        return client.post(f"/api/diagnostics/rtc-audio/{channel}", headers=headers,
                           content=b"\x1a\x45\xdf\xa3" + batch.encode())

    def complete(batch):
        for channel in ("computer", "robot-raw", "robot-clean"):
            assert upload(batch, channel).status_code == 200
        response = upload(batch, "report")
        assert response.status_code == 200 and response.json()["complete"]
        return response.json()

    first, partial = "00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002"
    complete(first)
    latest = client.get("/artifacts/rtc-diagnostic-latest.json").json()
    assert upload(partial, "computer").status_code == 200
    assert upload(partial, "report").status_code == 409
    assert client.get("/artifacts/rtc-diagnostic-latest.json").json() == latest
    batches = ["00000000-0000-4000-8000-000000000003", "00000000-0000-4000-8000-000000000004"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(complete, batches))
    for batch, result in zip(batches, results):
        for channel, url in result["files"].items():
            response = client.get(url)
            if channel != "report":
                assert response.content == b"\x1a\x45\xdf\xa3" + batch.encode()
                assert response.headers["content-type"] == "audio/webm"
            else:
                assert response.json()["recordingId"] == batch
        assert upload(batch, "computer").status_code == 409
    assert client.post("/api/diagnostics/rtc-audio/computer", content=b"x",
                       headers={"X-Recording-Id": "../invalid"}).status_code == 400


@pytest.mark.parametrize("failed_suffix", ["-manifest.json", "-latest.json"])
def test_diagnostic_publish_can_retry_identical_report_after_io_failure(tmp_path, failed_suffix):
    module = _load_service_module()
    service = _service(module, tmp_path)
    first, second = "00000000-0000-4000-8000-000000000005", "00000000-0000-4000-8000-000000000006"

    def audio(batch):
        for channel in ("computer", "robot-raw", "robot-clean"):
            service.save_rtc_audio_diagnostic(channel, batch, b"\x1a\x45\xdf\xa3fixture")

    def report(batch):
        return json.dumps({"recordingId": batch, "samples": []}).encode()

    audio(first)
    service.save_rtc_audio_diagnostic("report", first, report(first))
    latest = service.artifact_path("rtc-diagnostic-latest.json").read_bytes()
    audio(second)
    original = service._write_diagnostic_atomic

    def fail_publish(path, data):
        if path.name.endswith(failed_suffix):
            raise OSError("simulated publication failure")
        original(path, data)

    service._write_diagnostic_atomic = fail_publish
    with pytest.raises(OSError, match="publication failure"):
        service.save_rtc_audio_diagnostic("report", second, report(second))
    assert service.artifact_path("rtc-diagnostic-latest.json").read_bytes() == latest
    service._write_diagnostic_atomic = original
    assert service.save_rtc_audio_diagnostic("report", second, report(second))["complete"] is True
    assert json.loads(service.artifact_path("rtc-diagnostic-latest.json").read_bytes())["recording_id"] == second
    with pytest.raises(module.HTTPException) as rejected:
        service.save_rtc_audio_diagnostic("report", second, report(second).replace(b"[]", b"[1]"))
    assert rejected.value.status_code == 409


@pytest.mark.parametrize("change_device", [False, True])
def test_scenario_discards_inconsistent_resource_evidence(tmp_path, monkeypatch, change_device):
    module = _load_service_module()
    robot = _procedural_robot()
    robot.resource_snapshot_received_at = 100.0
    service = _service(module, tmp_path, robot)
    original = module.deepcopy
    captured = False

    def copy_then_update(value):
        nonlocal captured
        copied = original(value)
        if value is robot.resource_snapshot and not captured:
            captured = True
            robot.resource_snapshot_received_at = 101.0
            robot.resource_snapshot = {"sequence": 8, "captured_at_ms": 1235}
            if change_device:
                robot.device_info = {"device_id": "other-device"}
        return copied

    monkeypatch.setattr(module, "deepcopy", copy_then_update)
    service.start_scenario_recording()
    sample = service.scenario_report()["samples"][0]
    assert sample["telemetry"]["status"] == "unavailable"
    assert sample["procedural"]["telemetry_available"] is False
    assert service.scenario_report()["summary"]["memory"] == {}


def test_late_procedural_cleanup_preserves_a_new_client_owner(tmp_path):
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    client = TestClient(module.create_web_app(service, web_root=LAB_ROOT / "web"))
    first, second = "procedural-client-a", "procedural-client-b"
    assert client.post("/api/controls/procedural/start", json={"request_id": first}).json()["started"] is True
    assert client.post("/api/controls/procedural/stop").json()["state"] == "idle"
    assert client.post("/api/controls/procedural/start", json={"request_id": second}).json()["started"] is True
    before = list(robot.expression_runtime.calls)
    stale = client.post("/api/controls/procedural/stop", json={"request_id": first})
    assert stale.status_code == 200 and stale.json()["matched"] is False
    assert robot.expression_runtime.calls == before
    assert service.status()["procedural"]["state"] == "running"
    assert client.post("/api/controls/procedural/stop", json={"request_id": second}).json()["state"] == "idle"


def test_background_sampler_continues_while_maintenance_waits_for_its_own_stop_ack(tmp_path, monkeypatch):
    import time
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    service.start_procedural()
    service.start_scenario_recording()
    service._set_procedural_state("stop_required")
    entered, release = threading.Event(), threading.Event()
    original = robot.expression_runtime.set_audio_follow

    def delayed(enabled):
        if not enabled:
            entered.set()
            assert release.wait(timeout=4.0)
        return original(enabled)

    robot.expression_runtime.set_audio_follow = delayed
    monkeypatch.setattr(module, "_SCENARIO_SAMPLE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(module, "_MAINTENANCE_INTERVAL_SECONDS", 0.02)
    with TestClient(module.create_web_app(service, web_root=LAB_ROOT / "web")):
        try:
            assert entered.wait(timeout=1.0)
            deadline = time.monotonic() + 1.0
            while service.scenario_recording_status()["sample_count"] < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert service.scenario_recording_status()["sample_count"] >= 3
        finally:
            release.set()


@pytest.mark.parametrize("domain,stopping", [("procedural", False), ("procedural", True), ("sd_baseline", False), ("sd_baseline", True)])
def test_expression_transition_ack_does_not_block_status_or_recording(tmp_path, domain, stopping):
    module = _load_service_module()
    robot = _sd_baseline_robot()
    service = _service(module, tmp_path, robot)
    if stopping:
        getattr(service, f"start_{domain}")()
    service.start_scenario_recording()
    service._recording_last_sample_at = None
    entered, release, ready = threading.Event(), threading.Event(), threading.Event()
    errors = []
    target, method = (robot.expression_runtime, "set_audio_follow") if domain == "procedural" else (robot.behavior, "stop" if stopping else "play")
    original = getattr(target, method)

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3.0)
        return original(*args, **kwargs)

    setattr(target, method, blocked)

    def transition():
        try:
            getattr(service, f"{'stop' if stopping else 'start'}_{domain}")()
        except Exception as error:
            errors.append(error)

    def inspect():
        try:
            assert service.status()[domain]["state"] == ("stop_required" if stopping else "starting")
            service.maintain()
            assert service.scenario_recording_status()["sample_count"] == 2
            ready.set()
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=transition)
    worker.start()
    assert entered.wait(timeout=1.0)
    reader = threading.Thread(target=inspect)
    reader.start()
    try:
        assert ready.wait(timeout=1.0)
    finally:
        release.set()
        worker.join(timeout=3.0)
        reader.join(timeout=3.0)
    assert not errors and not worker.is_alive() and not reader.is_alive()


@pytest.mark.parametrize("stopping", [False, True])
def test_old_scenario_sample_cannot_enter_a_new_recording(tmp_path, stopping):
    module = _load_service_module()
    service = _service(module, tmp_path, _procedural_robot())
    service.start_scenario_recording(label="old")
    service._recording_last_sample_at = None
    entered, release = threading.Event(), threading.Event()
    original = service._rtc.snapshot

    def snapshot():
        if threading.current_thread().name == "old-sampler":
            entered.set()
            assert release.wait(timeout=3.0)
        return original()

    service._rtc.snapshot = snapshot
    worker = threading.Thread(target=service.stop_scenario_recording if stopping else service._sample_scenario, name="old-sampler")
    worker.start()
    assert entered.wait(timeout=1.0)
    try:
        service.stop_scenario_recording()
        service.start_scenario_recording(label="new")
        service._recording_last_sample_at = None
    finally:
        release.set()
        worker.join(timeout=3.0)
    assert not worker.is_alive()
    assert service.scenario_recording_status()["sample_count"] == 1
    assert service.scenario_recording_status()["active"] is True


def test_generic_inference_owns_only_camera_and_retries_stop(tmp_path):
    module = _load_service_module()
    robot = _robot()
    robot.capabilities += ("vision.models.v1", "vision.inference.v1")
    class Session:
        id = 123
        model_id = 2
        fail_stop = True
        def latest(self, **kwargs): return None
        def close(self, **kwargs):
            if self.fail_stop: raise TimeoutError("stop unconfirmed")
    session = Session()
    robot.vision = SimpleNamespace(start_inference=lambda model, **kw: session,
                                  stop_inference=lambda **kw: session.close())
    service = _service(module, tmp_path, robot)
    client = _client_for_service(module, tmp_path, service)
    assert client.post("/api/vision/inference/start", json={"model_id": 2}).status_code == 200
    assert service.status()["resource_owners"] == {"camera": "vision_inference"}
    assert client.get("/api/vision/inference/result").json() == {"ready": False}
    with pytest.raises(TimeoutError): service.stop_inference()
    assert service.status()["inference"]["state"] == "stop_required"
    assert "camera" in service.status()["resource_owners"]
    session.fail_stop = False
    service.stop_inference()
    assert service.status()["inference"]["state"] == "idle"
    assert not service.status()["resource_owners"]


def test_generic_preview_http_returns_same_frame_and_only_owns_camera(tmp_path):
    import base64
    from watcherobot.inference import InferenceResult, DetectionBox
    module = _load_service_module()
    robot = _robot()
    calls = []
    result = InferenceResult(123, 3, 9, 100, 640, 480, "detection",
                             (DetectionBox(100, 120, 80, 60, 90, 2),), b"\xff\xd8image\xff\xd9")
    session = SimpleNamespace(id=123, model_id=3, latest=lambda **kw: result)
    def start(model_id, **options):
        calls.append((model_id, options))
        return session
    robot.vision = SimpleNamespace(start_inference=start, stop_inference=lambda: None)
    robot.capabilities += ("vision.inference.v1", "vision.inference.preview.v1")
    service = _service(module, tmp_path, robot)
    client = _client_for_service(module, tmp_path, service)
    assert client.post("/api/vision/inference/start", json={"model_id": 3, "preview": True}).status_code == 200
    assert calls == [(3, {"preview": True})]
    frame = client.get("/api/vision/inference/result").json()
    assert frame["sequence"] == 9 and frame["model_id"] == 3
    assert base64.b64decode(frame["jpeg_base64"]) == result.jpeg
    assert frame["boxes"][0]["x"] == 100
    assert service.status()["resource_owners"] == {"camera": "vision_inference"}
    service.stop_inference()
    assert not service.status()["resource_owners"]
