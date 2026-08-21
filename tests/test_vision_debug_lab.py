from __future__ import annotations

import importlib.util
import json
import struct
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).parents[1]
LAB_ROOT = ROOT / "examples" / "vision_debug_lab"


def _load_service_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "watcherobot_vision_debug_lab_service",
        LAB_ROOT / "service.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _vision_status(
    *,
    backend: str = "sscma",
    health: str = "ready",
    connected: bool = True,
    inference: bool = True,
    preview: bool = True,
    model_name: str | None = "Face Detection",
    contains_face: bool = True,
) -> SimpleNamespace:
    model = (
        SimpleNamespace(
            model_id=4,
            name=model_name,
            task="detect",
            contains_face_class=contains_face,
        )
        if model_name is not None
        else None
    )
    return SimpleNamespace(
        backend=backend,
        health=health,
        status_code=0,
        initialized=True,
        connected=connected,
        streaming=False,
        inferencing=False,
        capabilities=SimpleNamespace(
            capture=True,
            preview=preview,
            inference=inference,
            model_info=model is not None,
            model_management=False,
        ),
        model=model,
    )


def _frame(sequence: int, *, faces: int = 1) -> SimpleNamespace:
    face_values = tuple(
        SimpleNamespace(
            x=80,
            y=70,
            width=48,
            height=52,
            score=86,
            target=1,
        )
        for _ in range(faces)
    )
    telemetry = SimpleNamespace(
        sequence=sequence,
        timestamp_ms=sequence * 67,
        age_ms=9,
        frame_width=416,
        frame_height=416,
        faces=face_values,
        target_visible=bool(faces),
        error_x_percent=4.5,
        error_y_percent=-7.25,
        pan_velocity_deg_s=3.0,
        tilt_velocity_deg_s=-4.0,
        state=1,
        command=1,
        preprocess_ms=1.0,
        inference_ms=33.0,
        postprocess_ms=1.0,
    )
    return SimpleNamespace(
        jpeg=b"\xff\xd8vision-frame" + bytes([sequence]) + b"\xff\xd9",
        sequence=sequence,
        device_timestamp_ms=sequence * 67,
        received_at=time.time(),
        width=416,
        height=416,
        faces=face_values,
        telemetry=telemetry,
    )


class FakePreview:
    def __init__(self, frames: list[SimpleNamespace]) -> None:
        self.frames = list(frames)
        self.closed = False
        self.dropped_frames = 2

    def read(self, timeout: float | None = None) -> SimpleNamespace:
        del timeout
        if self.closed:
            raise RuntimeError("preview closed")
        if self.frames:
            time.sleep(0.01)
            return self.frames.pop(0)
        time.sleep(0.005)
        raise TimeoutError("no frame")

    def close(self) -> None:
        self.closed = True


class FakeVision:
    def __init__(self, status: SimpleNamespace) -> None:
        self.value = status
        self.calls = 0

    def status(self, *, timeout: float | None = None) -> SimpleNamespace:
        del timeout
        self.calls += 1
        return self.value


class FakeFaceTracking:
    def __init__(self, frames: list[SimpleNamespace]) -> None:
        self.preview = FakePreview(frames)
        self.open_calls: list[dict[str, object]] = []
        self.stop_calls: list[str] = []

    def open_preview(self, **kwargs: object) -> FakePreview:
        self.open_calls.append(kwargs)
        return self.preview

    def stop(self, *, policy: str) -> None:
        self.stop_calls.append(policy)
        self.preview.close()


class FakeRobot:
    def __init__(
        self,
        status: SimpleNamespace,
        frames: list[SimpleNamespace] | None = None,
    ) -> None:
        self.vision = FakeVision(status)
        self.face_tracking = FakeFaceTracking(frames or [])


def _device_status() -> dict[str, object]:
    return {
        "online": True,
        "state": "connected",
        "request_id": "request-1",
        "last_error": None,
    }


def _wait_for(predicate: object, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return
        time.sleep(0.01)
    raise AssertionError("condition was not satisfied")


def test_status_explains_backend_and_non_face_model_root_causes(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = FakeRobot(
        _vision_status(
            backend="ptl",
            inference=False,
            preview=False,
            model_name=None,
        )
    )
    service = module.VisionDebugLabService(
        robot=robot,
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )

    snapshot = service.status()

    assert snapshot["vision"]["backend"] == "ptl"
    assert snapshot["vision"]["capabilities"]["inference"] is False
    assert {item["code"] for item in snapshot["findings"]} >= {
        "backend_no_inference",
        "model_unavailable",
    }


def test_preview_rejects_a_model_without_face_class(tmp_path: Path) -> None:
    module = _load_service_module()
    service = module.VisionDebugLabService(
        robot=FakeRobot(_vision_status(model_name="Person Detection", contains_face=False)),
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )

    with pytest.raises(module.VisionLabPreflightError, match="face class"):
        service.start_preview(width=416, height=416, frame_stride=1, stop_policy="hold")


def test_preview_publishes_same_sequence_binary_packet_and_metrics(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = FakeRobot(_vision_status(), [_frame(7), _frame(8, faces=0)])
    service = module.VisionDebugLabService(
        robot=robot,
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )

    service.start_preview(width=416, height=416, frame_stride=1, stop_policy="hold")
    generation, packet = service.wait_for_packet(after_generation=0, timeout=1.0)
    metadata, jpeg = module.decode_preview_packet(packet)

    assert generation >= 1
    assert metadata["sequence"] == 7
    assert metadata["telemetry"]["sequence"] == 7
    assert metadata["application_ingress_ms"] >= 0
    assert metadata["faces"][0]["score"] == 86
    assert jpeg == _frame(7).jpeg
    _wait_for(lambda: service.status()["session"]["frames"] >= 2)
    snapshot = service.status()
    assert snapshot["session"]["frames"] == 2
    assert snapshot["session"]["face_frames"] == 1
    assert snapshot["session"]["inference_avg_ms"] == 33.0
    assert snapshot["session"]["application_p95_ms"] is not None
    service.stop_preview(policy="hold")
    assert robot.face_tracking.stop_calls == ["hold"]


def test_preview_packet_waiter_can_skip_backlog_for_browser_rendering(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    service = module.VisionDebugLabService(
        robot=FakeRobot(_vision_status(), [_frame(1), _frame(2), _frame(3)]),
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )

    service.start_preview(width=416, height=416, frame_stride=1, stop_policy="hold")
    _wait_for(lambda: service.status()["session"]["frames"] >= 3)
    generation, packet = service.wait_for_packet(
        after_generation=0,
        timeout=1.0,
        latest=True,
    )
    metadata, _jpeg = module.decode_preview_packet(packet)
    service.stop_preview(policy="hold")

    assert generation == 3
    assert metadata["sequence"] == 3


def test_second_preview_start_reports_busy_before_rechecking_model(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    robot = FakeRobot(_vision_status(), [])
    service = module.VisionDebugLabService(
        robot=robot,
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )
    service.start_preview(width=416, height=416, frame_stride=1, stop_policy="hold")
    robot.vision.value = _vision_status(model_name=None)

    with pytest.raises(module.VisionLabBusyError, match="already running"):
        service.start_preview(
            width=416,
            height=416,
            frame_stride=1,
            stop_policy="hold",
        )

    service.stop_preview(policy="hold")


def test_stopped_session_keeps_stable_fps_and_stride_skips_are_not_missing(
    tmp_path: Path,
) -> None:
    module = _load_service_module()
    robot = FakeRobot(_vision_status(), [_frame(10), _frame(12), _frame(14)])
    service = module.VisionDebugLabService(
        robot=robot,
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )

    service.start_preview(width=240, height=240, frame_stride=2, stop_policy="hold")
    _wait_for(lambda: service.status()["session"]["frames"] >= 3)
    stopped = service.stop_preview(policy="hold")
    time.sleep(0.03)
    later = service.status()["session"]

    assert stopped["missing_sequences"] == 0
    assert later["missing_sequences"] == 0
    assert later["fps"] == stopped["fps"]
    assert later["elapsed_seconds"] == stopped["elapsed_seconds"]


def test_recording_writes_jpeg_jsonl_and_manifest(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = FakeRobot(_vision_status(), [_frame(1), _frame(2)])
    service = module.VisionDebugLabService(
        robot=robot,
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )

    service.start_preview(width=416, height=416, frame_stride=1, stop_policy="hold")
    recording = service.start_recording()
    _wait_for(lambda: service.status()["session"]["frames"] >= 2)
    result = service.stop_recording()
    service.stop_preview(policy="hold")

    recording_dir = tmp_path / str(recording["relative_path"])
    lines = recording_dir.joinpath("frames.jsonl").read_text(encoding="utf-8").splitlines()
    manifest = json.loads(recording_dir.joinpath("manifest.json").read_text(encoding="utf-8"))
    assert len(lines) == 2
    assert json.loads(lines[0])["sequence"] == 1
    assert recording_dir.joinpath("frames", "00000001.jpg").read_bytes() == _frame(1).jpeg
    assert result["frames"] == 2
    assert manifest["frames"] == 2


def test_last_viewer_disconnect_holds_preview_after_grace_period(tmp_path: Path) -> None:
    module = _load_service_module()
    robot = FakeRobot(_vision_status(), [])
    service = module.VisionDebugLabService(
        robot=robot,
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
        disconnect_grace_seconds=0.02,
    )
    service.start_preview(width=416, height=416, frame_stride=1, stop_policy="recenter")

    service.viewer_connected()
    service.viewer_disconnected()

    _wait_for(lambda: robot.face_tracking.stop_calls == ["hold"])
    assert service.status()["session"]["running"] is False


def test_diagnostic_export_contains_vision_metrics_and_findings(tmp_path: Path) -> None:
    module = _load_service_module()
    service = module.VisionDebugLabService(
        robot=FakeRobot(_vision_status()),
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )

    result = service.export_diagnostic_report()
    report = json.loads((tmp_path / str(result["relative_path"])).read_text(encoding="utf-8"))

    assert report["schema"] == "watcher.vision-debug-report.v1"
    assert report["connection"]["online"] is True
    assert report["vision"]["model"]["name"] == "Face Detection"
    assert isinstance(report["findings"], list)


def test_web_app_exposes_loopback_dashboard_preview_and_safety_headers(tmp_path: Path) -> None:
    module = _load_service_module()
    service = module.VisionDebugLabService(
        robot=FakeRobot(_vision_status()),
        artifacts_dir=tmp_path,
        device_status_provider=_device_status,
    )
    web_root = LAB_ROOT / "web"

    with TestClient(module.create_web_app(service, web_root=web_root)) as client:
        response = client.get("/", headers={"Host": "127.0.0.1:43210"})
        status = client.get(
            "/api/status",
            headers={"Host": "127.0.0.1:43210"},
        )
        rejected = client.get("/", headers={"Host": "vision.example.com"})

    assert response.status_code == 200
    assert "Vision Debug Lab" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert status.json()["vision"]["backend"] == "sscma"
    assert rejected.status_code == 421


def test_packet_decoder_rejects_malformed_frames() -> None:
    module = _load_service_module()

    with pytest.raises(ValueError, match="preview packet"):
        module.decode_preview_packet(b"bad")
    with pytest.raises(ValueError, match="preview packet"):
        module.decode_preview_packet(b"VDL1" + struct.pack("<I", 500) + b"{}")
