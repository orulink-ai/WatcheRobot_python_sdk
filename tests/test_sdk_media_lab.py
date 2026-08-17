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

    def capture(self, **kwargs: int | float) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(data=b"\xff\xd8media-lab\xff\xd9")


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
        capabilities=(
            "motion",
            "light",
            "animation",
            "animation.prefetch.v1",
            "audio.stream",
            "microphone",
            "camera.capture",
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
        "history": service._robot.resource_history,
    }
    assert status["rtc"]["stats"] == {}
    assert status["animations"] == ["boot", "happy", "thinking", "standby_little4"]


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


def test_same_media_resource_is_serialized_while_independent_camera_remains_available(
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
    with pytest.raises(module.MediaLabBusyError, match="play_audio"):
        service.play_audio()
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
    assert "最低 ${browserLatency.minimumMs} ms" in javascript


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
    assert 'navigator.sendBeacon(rtcEndpoint("stop", mode))' in javascript
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
    assert 'JSON.stringify({ mode })' in javascript
    assert "isCurrentRtcGeneration(state.rtc.generation, generation)" in javascript
    assert "pollRtcEvents(generation)" in javascript


def test_media_lab_stop_closes_browser_media_before_waiting_for_device_release() -> None:
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    stop_body = javascript.split("async function stopRtcSession()", 1)[1].split(
        "async function failRtcSession", 1
    )[0]

    assert stop_body.index("cleanupRtcSession();") < stop_body.index(
        'await api(rtcEndpoint("stop", mode), { method: "POST" });'
    )
    assert "本地音视频已结束，设备释放确认超时" in stop_body
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
    assert "扬声器或动画音效正在播放，请停止后再开启" in javascript


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
    assert "参考累计处理" in javascript
    assert "rtcAudioAec" in javascript
    assert "id=\"rtcAudioAec\"" in document
    assert "物理麦克风峰值" in document


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


def test_local_ui_preserves_chinese_source_copy_behind_an_english_default() -> None:
    html = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")
    i18n = LAB_ROOT.joinpath("web", "i18n.mjs").read_text(encoding="utf-8")

    assert '<html lang="en" data-i18n-ready="false">' in html
    assert 'defaultLocale: "en-US"' in javascript
    for copy in (
        "SDK 测试台",
        "连接机器人",
        "输入设备屏幕上的六位配对码",
        "运行基础全检",
        "云台姿态",
        "机身灯效",
        "扬声器流式播放",
        "资源生命周期",
        "释放结果",
        "相机拍照",
        "相机实时画面",
        "开启实时画面",
        "全双工音频通话",
        "开启全双工通话",
        "麦克风录音",
        "设备能力矩阵",
        "运行日志",
        "等待测试",
    ):
        assert copy in html

    for copy in (
        "设备在线",
        "配对码必须是 6 位数字",
        "正在发现设备",
        "系统空闲",
        "正在传输 PCM 示例音频",
        "rtc-control",
        "mjpeg_websocket_url",
        "new WebSocket(url)",
        'socket.send("ready")',
        "parseWjpgPacket",
        'api("/api/video/session/start"',
        "navigator.mediaDevices.getUserMedia",
        "createRtcMicrophoneConstraints",
        "rtc_audio_processing",
        'window.location.hostname === "127.0.0.1"',
        'params.get("rtc_hil") === "1"',
        "createMediaStreamDestination",
        "createOscillator",
        "state.rtc.diagnosticAudio",
        "for (const track of localStream.getTracks()) track.stop();",
        "state.rtc.generation !== generation",
        'api("/api/rtc/session/start"',
        'addEventListener("track"',
        "本地音视频已结束，设备释放确认超时",
        "基础全检通过",
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

    assert "SDK 媒体实验室" not in html + javascript + i18n

    assert "localResources: new Set()" in javascript
    assert "actionResources.some((name) => state.localResources.has(name))" in javascript
    assert "const ownsResource = !interrupt;" in javascript
    assert 'resource: "motion"' in javascript
    assert 'resource: "light"' in javascript
    assert 'resource: "animation"' in javascript
    assert 'resource: "speaker"' in javascript
    assert 'resources: ["camera", "animation"]' in javascript
    assert 'resource: "microphone"' in javascript
    assert 'path: "/api/actions/stop-audio"' in javascript
    assert 'path: "/api/controls/motion/stop"' in javascript
    assert javascript.count("interrupt: true") >= 2
    assert "设备已断开，请重新连接后再测试" in javascript
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
