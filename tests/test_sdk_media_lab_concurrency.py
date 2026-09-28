import threading
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from tests.test_sdk_media_lab import _client_for_service, _load_service_module, _robot, _service
from watcherobot.robot import ExpressionRuntimeDomain


def test_legacy_ui_uses_lightweight_eye_frames():
    module = _load_service_module()
    for index in range(4):
        payload = module._concurrency_expression(index)
        ExpressionRuntimeDomain._build_payload(**payload)
        assert "custom_vector_path" not in payload
        assert "sphere_strength" not in payload
        assert payload["transition_ms"] == 120
    assert module._concurrency_expression(0) == module._concurrency_expression(4)
    assert module._concurrency_expression(0)["gaze_x"] == -0.65


def _stress_robot():
    robot = _robot()
    robot.capabilities += ("expression.runtime.v3",)
    robot.expression_runtime = SimpleNamespace(start=lambda **kw: None, update=lambda **kw: None, stop=lambda: None)
    robot.camera.capture = lambda **kw: SimpleNamespace(data=_jpeg())
    return robot


def _jpeg():
    buffer = BytesIO()
    Image.new("RGB", (16, 12), "blue").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_combined_test_overlaps_all_workers_and_excludes_other_actions(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    service = _service(module, tmp_path, robot)
    barrier = threading.Barrier(3, timeout=3)
    calls = set()

    def meet(name):
        if name not in calls:
            calls.add(name)
            barrier.wait()
            with pytest.raises(module.MediaLabBusyError):
                service.capture_photo_with_feedback()

    robot.expression_runtime.update = lambda **kw: meet("ui")
    robot.audio.play_file = lambda path: (meet("speaker") or SimpleNamespace(wait=lambda timeout: None))
    def capture(**kwargs):
        meet("camera")
        time.sleep(0.02)
        return SimpleNamespace(data=_jpeg())

    robot.camera.capture = capture
    report = service.run_concurrency_test(duration=1)
    assert report["passed"] is True
    assert calls == {"ui", "speaker", "camera"}
    assert all(report["counts"][name] > 0 for name in calls)
    assert report["counts"]["camera"] == 1
    assert report["counts"]["speaker"] == 1
    for name in calls:
        result = report["results"][name]
        assert result["status"] == "passed"
        assert result["attempted"] == result["succeeded"] > 0
        assert result["failed"] == 0
        assert result["max_ms"] >= result["average_ms"] >= 0
    assert report["results"]["camera"]["last_image"]["width"] == 16
    assert report["results"]["speaker"]["physical_confirmation"] == "not_verified"
    assert report["results"]["ui"]["physical_confirmation"] == "not_verified"
    assert not service.status()["resource_owners"]
    assert (tmp_path / "artifacts" / report["report"]).is_file()


def test_failed_capture_stops_load_and_cleans_up(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    stopped = []
    robot.expression_runtime.stop = lambda: stopped.append("ui")
    robot.audio.stop = lambda: stopped.append("speaker")
    robot.camera.capture = lambda **kw: (_ for _ in ()).throw(TimeoutError("no JPEG"))
    service = _service(module, tmp_path, robot)
    report = service.run_concurrency_test(duration=1)
    assert report["passed"] is False
    assert any(item["worker"] == "camera" for item in report["errors"])
    assert report["results"]["camera"]["status"] == "failed"
    assert report["results"]["camera"]["failed"] == 1
    assert report["results"]["camera"]["last_error"] == "no JPEG"
    assert set(stopped) == {"ui", "speaker"}
    assert not service.status()["resource_owners"]


def test_unsupported_ui_never_starts_hardware(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path)
    with pytest.raises(module.MediaLabCapabilityError):
        service.run_concurrency_test(duration=1)
    assert not service.status()["resource_owners"]


def test_stop_and_cleanup_failure_are_not_reported_as_passed(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    service = _service(module, tmp_path, robot)
    robot.expression_runtime.update = lambda **kw: service.stop_concurrency_test()
    robot.expression_runtime.stop = lambda: (_ for _ in ()).throw(TimeoutError("stop unconfirmed"))
    report = service.run_concurrency_test(duration=1)
    assert report["cancelled"] is True
    assert report["passed"] is False
    assert any(item["worker"] == "ui_cleanup" for item in report["errors"])
    assert report["results"]["ui"]["status"] == "cleanup_failed"


def test_invalid_jpeg_is_a_failed_photo_not_a_success(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    robot.camera.capture = lambda **kw: SimpleNamespace(data=b"not an image")
    report = _service(module, tmp_path, robot).run_concurrency_test(duration=1)
    assert report["counts"]["camera"] == 0
    assert report["results"]["camera"]["failed"] == 1
    assert report["passed"] is False


def test_ui_start_failure_does_not_skip_other_initial_attempts(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    robot.expression_runtime.start = lambda **kw: (_ for _ in ()).throw(TimeoutError("UI start timed out"))
    report = _service(module, tmp_path, robot).run_concurrency_test(duration=1)
    assert report["results"]["ui"]["status"] == "failed"
    assert report["results"]["ui"]["last_error"] == "UI start timed out"
    assert report["results"]["camera"]["attempted"] == 1
    assert report["results"]["speaker"]["attempted"] == 1
    assert report["results"]["camera"]["status"] == "interrupted"
    assert report["results"]["speaker"]["status"] == "interrupted"


def test_empty_timeout_message_and_cleanup_failure_are_explicit(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    robot.expression_runtime.start = lambda **kw: (_ for _ in ()).throw(TimeoutError())
    robot.audio.stop = lambda: (_ for _ in ()).throw(TimeoutError())
    report = _service(module, tmp_path, robot).run_concurrency_test(duration=1)
    assert report["results"]["ui"]["last_error"] == "TimeoutError"
    assert report["results"]["speaker"]["status"] == "cleanup_failed"
    assert report["results"]["speaker"]["cleanup_error"] == "TimeoutError"


@pytest.mark.parametrize("duration", [0, 121, -1])
def test_http_duration_is_bounded(tmp_path, duration):
    module = _load_service_module()
    client = _client_for_service(module, tmp_path, _service(module, tmp_path))
    assert client.post("/api/concurrency/start", json={"duration": duration}).status_code == 422


def test_legacy_policy_waits_after_photos_and_plays_audio_once(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    starts, ends, audio_calls, ui_calls = [], [], [], []
    def capture(**kwargs):
        starts.append(time.monotonic())
        time.sleep(0.05)
        ends.append(time.monotonic())
        return SimpleNamespace(data=_jpeg())
    robot.camera.capture = capture
    robot.audio.play_file = lambda path: (audio_calls.append(path) or SimpleNamespace(wait=lambda timeout: None))
    robot.expression_runtime.start = lambda **kw: ui_calls.append(("start", kw))
    robot.expression_runtime.update = lambda **kw: ui_calls.append(("update", kw))
    service = _service(module, tmp_path, robot)
    report = service.run_concurrency_test(duration=2.3)
    assert len(starts) >= 2
    assert all(start - end >= 0.98 for start, end in zip(starts[1:], ends))
    assert len(audio_calls) == 1
    assert ui_calls[0][1] == dict(preset="thinking", style="watcher_pulse", color="#A1F03C", gaze_x=0.0, gaze_y=0.0, auto_blink=True, transition_ms=0)
    assert ui_calls[1][1] == module._concurrency_expression(0)
    assert report["profile"]["camera_interval_s"] == 1.0
    assert report["profile"]["audio_repetitions"] == 1
    assert report["profile"]["ui_interval_s"] == 0.18
    assert report["running"] is False


def test_progress_is_available_before_all_workers_finish(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    service = _service(module, tmp_path, robot)
    entered, release = threading.Event(), threading.Event()
    def capture(**kwargs):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(data=_jpeg())
    robot.camera.capture = capture
    thread = threading.Thread(target=lambda: service.run_concurrency_test(duration=1))
    thread.start()
    try:
        assert entered.wait(2)
        client = _client_for_service(module, tmp_path, service)
        report = client.get("/api/concurrency/result").json()
        assert report["running"] is True
        assert report["counts"]["camera"] == 0
        assert report["results"]["camera"]["status"] == "running"
    finally:
        release.set()
        thread.join(4)
    assert not thread.is_alive()
    assert service._concurrency_report["running"] is False


def test_concurrent_artifacts_are_downloadable_without_exposing_other_files(tmp_path):
    module = _load_service_module()
    service = _service(module, tmp_path, _stress_robot())
    report = service.run_concurrency_test(duration=1)
    client = _client_for_service(module, tmp_path, service)
    response = client.get(f"/artifacts/{report['report']}")
    assert response.status_code == 200
    assert response.json()["run_id"] == report["run_id"]
    photo = report["results"]["camera"]["last_image"]["file"]
    response = client.get(f"/artifacts/{photo}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(response.content)) as decoded:
        decoded.load()
    (tmp_path / "artifacts" / "private.json").write_text("{}")
    assert client.get("/artifacts/private.json").status_code == 404
    assert service.artifact_path("../private.json") is None


def test_slow_start_without_ui_updates_is_incomplete(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    robot.expression_runtime.start = lambda **kw: time.sleep(1.05)
    report = _service(module, tmp_path, robot).run_concurrency_test(duration=1)
    assert report["counts"]["ui"] == 1
    assert report["passed"] is False
    assert report["results"]["ui"]["status"] == "incomplete"
    assert report["results"]["ui"]["updates_succeeded"] == 0


@pytest.mark.parametrize("worker,resources", [("ui", {"animation"}), ("speaker", {"microphone", "speaker"})])
def test_failed_cleanup_holds_resources_until_retry_succeeds(tmp_path, worker, resources):
    module = _load_service_module()
    robot = _stress_robot()
    domain = robot.expression_runtime if worker == "ui" else robot.audio
    domain.stop = lambda: (_ for _ in ()).throw(TimeoutError("stop not confirmed"))
    service = _service(module, tmp_path, robot)
    report = service.run_concurrency_test(duration=1)
    assert report["results"][worker]["status"] == "cleanup_failed"
    assert set(service.status()["resource_owners"]) == resources
    for resource in resources:
        with pytest.raises(module.MediaLabBusyError):
            with service._operation("conflicting_action", resources=(resource,)):
                pass
    # A failed retry retains the lease; a successful retry releases only this lease.
    assert service.retry_concurrency_cleanup()["pending"]
    domain.stop = lambda: None
    assert service.retry_concurrency_cleanup()["pending"] == {}
    assert service.status()["resource_owners"] == {}
    assert report["results"][worker]["cleanup_error"] == "stop not confirmed"


def test_disconnect_releases_pending_cleanup_without_sending_commands(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    robot.expression_runtime.stop = lambda: (_ for _ in ()).throw(TimeoutError("stop not confirmed"))
    service = _service(module, tmp_path, robot)
    service.run_concurrency_test(duration=1)
    assert service.status()["busy"]
    service._device_status_provider = lambda: {"online": False, "state": "disconnected"}
    service.maintain()
    assert service.status()["resource_owners"] == {}
    assert service.status()["concurrency_cleanup"] == {}


def test_shutdown_interrupts_audio_wait_and_blocks_new_work(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    entered = threading.Event()
    waits = []
    def wait(timeout):
        entered.set()
        waits.append(timeout)
        time.sleep(min(timeout, 0.1))
        raise TimeoutError("not finished yet")
    robot.audio.play_file = lambda path: SimpleNamespace(wait=wait)
    service = _service(module, tmp_path, robot)
    reports = []
    thread = threading.Thread(target=lambda: reports.append(service.run_concurrency_test(duration=30)))
    thread.start()
    try:
        assert entered.wait(2)
        service.request_shutdown()
    finally:
        service.stop_concurrency_test()
        thread.join(3)
    assert not thread.is_alive()
    assert reports[0]["cancelled"]
    assert reports[0]["results"]["speaker"]["status"] == "interrupted"
    assert not reports[0]["errors"]
    assert max(waits) <= 0.2
    with pytest.raises(module.MediaLabBusyError):
        service.run_concurrency_test(duration=1)


def test_cleanup_endpoint_releases_only_confirmed_resources(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    robot.expression_runtime.stop = lambda: (_ for _ in ()).throw(TimeoutError("UI busy"))
    robot.audio.stop = lambda: (_ for _ in ()).throw(TimeoutError("audio busy"))
    service = _service(module, tmp_path, robot)
    service.run_concurrency_test(duration=1)
    client = _client_for_service(module, tmp_path, service)
    robot.expression_runtime.stop = lambda: None
    response = client.post("/api/concurrency/cleanup")
    assert response.status_code == 200
    assert response.json()["pending"] == {"speaker": "audio busy"}
    assert set(service.status()["resource_owners"]) == {"microphone", "speaker"}
    robot.audio.stop = lambda: None
    assert client.post("/api/concurrency/cleanup").json()["pending"] == {}
    assert not service.status()["busy"]


def test_stop_cleans_audio_and_ui_without_waiting_for_camera(tmp_path):
    module = _load_service_module()
    robot = _stress_robot()
    camera_entered = threading.Event()
    audio_stopped, ui_stopped = threading.Event(), threading.Event()
    def capture(**kwargs):
        camera_entered.set()
        assert audio_stopped.wait(2)
        assert ui_stopped.wait(2)
        return SimpleNamespace(data=_jpeg())
    def wait(timeout):
        time.sleep(min(timeout, .05))
        raise TimeoutError("playing")
    robot.camera.capture = capture
    robot.audio.play_file = lambda path: SimpleNamespace(wait=wait)
    robot.audio.stop = audio_stopped.set
    robot.expression_runtime.stop = ui_stopped.set
    service = _service(module, tmp_path, robot)
    reports = []
    thread = threading.Thread(target=lambda: reports.append(service.run_concurrency_test(duration=30)))
    thread.start()
    try:
        assert camera_entered.wait(1)
        service.stop_concurrency_test()
        assert audio_stopped.wait(1)
        assert ui_stopped.wait(1)
    finally:
        audio_stopped.set()
        ui_stopped.set()
        thread.join(3)
    assert not thread.is_alive()
    assert reports[0]["cancelled"]
    assert not reports[0]["errors"]
