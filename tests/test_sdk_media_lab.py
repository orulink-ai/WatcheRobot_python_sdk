from __future__ import annotations

import importlib.util
import json
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
            "audio.stream",
            "microphone",
            "camera.capture",
            "rtc.audio.full_duplex.v1",
            "rtc.video.mjpeg.v1",
        ),
        device_info={"firmware_version": "V3.1", "device_id": "watcher-test"},
        audio=FakeAudio(playback or FakePlayback()),
        camera=FakeCamera(),
        microphone=FakeMicrophone(),
        motion=FakeMotion(),
        lights=FakeLights(),
    )


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
    for filename in ("index.html", "app.js", "styles.css", "rtc-audio-health.mjs"):
        web_root.joinpath(filename).write_text(filename, encoding="utf-8")
    return TestClient(module.create_web_app(service, web_root=web_root))


def test_status_exposes_device_capabilities_and_idle_operation(tmp_path: Path) -> None:
    module = _load_service_module()
    service = _service(module, tmp_path)

    status = service.status()

    assert status["connected"] is True
    assert status["busy"] is False
    assert status["active_action"] is None
    assert status["capabilities"] == [
        "motion",
        "light",
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


def test_media_actions_are_serialized_and_report_the_active_action(tmp_path: Path) -> None:
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
        service.capture_photo()

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


def test_http_app_serves_rtc_audio_health_module(tmp_path: Path) -> None:
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
    client = TestClient(module.create_web_app(service, web_root=web_root))

    response = client.get("/assets/rtc-audio-health.mjs")

    assert response.status_code == 200
    assert "export const ready" in response.text


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


def test_live_video_stop_failure_keeps_media_operation_exclusive(tmp_path: Path) -> None:
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
        service.play_audio()


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


def test_maintenance_does_not_release_media_lock_while_live_video_is_starting(
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
        service.play_audio()


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

    assert "if (state.rtc.pendingFrame !== null) state.rtc.droppedFrames += 1;" in javascript
    assert "state.rtc.pendingFrame = frame;\n      state.rtc.droppedFrames += 1;" not in javascript


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

    service._operation_lock.acquire()
    service._active_action = "capture_photo"
    try:
        busy = client.post("/api/actions/play-audio")
    finally:
        service._active_action = None
        service._operation_lock.release()
    assert busy.status_code == 409
    assert busy.json() == {
        "error": "busy",
        "message": "media lab is busy with capture_photo",
    }


def test_local_ui_presents_complete_simplified_chinese_copy() -> None:
    html = LAB_ROOT.joinpath("web", "index.html").read_text(encoding="utf-8")
    javascript = LAB_ROOT.joinpath("web", "app.js").read_text(encoding="utf-8")

    assert '<html lang="zh-CN">' in html
    for copy in (
        "SDK 测试台",
        "连接机器人",
        "输入设备屏幕上的六位配对码",
        "运行基础全检",
        "云台姿态",
        "机身灯效",
        "扬声器流式播放",
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
        "mjpeg-data",
        "parseWjpgPacket",
        'api("/api/video/session/start"',
        "navigator.mediaDevices.getUserMedia",
        'window.location.hostname === "127.0.0.1"',
        'params.get("rtc_hil") === "1"',
        "createMediaStreamDestination",
        "createOscillator",
        "state.rtc.diagnosticAudio",
        "for (const track of localStream.getTracks()) track.stop();",
        "state.rtc.generation !== generation",
        'api("/api/rtc/session/start"',
        'addEventListener("track"',
        "停止失败，请重试",
        "基础全检通过",
    ):
        assert copy in javascript

    for untranslated_copy in (
        "RUN MEDIA CHECK",
        "Speaker stream",
        "Camera capture",
        "Microphone record",
        "Awaiting test",
        "SYSTEM IDLE",
        "SDK 媒体实验室",
    ):
        assert untranslated_copy not in html + javascript

    assert "const unavailable = busy || !status.connected" in javascript
    assert "if (state.localBusy && !interrupt) return null;" in javascript
    assert "const ownsBusyState = !interrupt;" in javascript
    assert 'path: "/api/actions/stop-audio"' in javascript
    assert 'path: "/api/controls/motion/stop"' in javascript
    assert javascript.count("interrupt: true") >= 2
    assert "设备已断开，请重新连接后再测试" in javascript
    assert 'api("/api/device/pair"' in javascript
    assert 'autocomplete="one-time-code"' in html
    assert 'name="device_ip"' in html
    assert "device_ip: deviceIp || null" in javascript
