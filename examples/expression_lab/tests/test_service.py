from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from watcherobot.errors import CommandError

from service import ExpressionLabService, create_web_app


class FakeExpressionRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def start(self, preset: str, **parameters: object) -> None:
        self.calls.append(("start", {"preset": preset, **parameters}))

    def update(self, **parameters: object) -> None:
        self.calls.append(("update", parameters))

    def stop(self) -> None:
        self.calls.append(("stop", {}))


class FailingStartRuntime(FakeExpressionRuntime):
    fail = False

    def start(self, preset: str, **parameters: object) -> None:
        super().start(preset, **parameters)
        if self.fail:
            raise RuntimeError("device rejected start")


def make_service(
    *,
    connected: bool = True,
    expression_supported: bool = True,
    pair_watcher=None,
    resource_snapshot=None,
) -> tuple[ExpressionLabService, FakeExpressionRuntime]:
    runtime = FakeExpressionRuntime()
    capabilities = ("expression.runtime.v1", "expression.runtime.v2") if expression_supported else ("animation",)
    robot = SimpleNamespace(
        expression_runtime=runtime,
        capabilities=capabilities if connected else (),
        device_info={"model": "Watcher"} if connected else {},
        resource_snapshot=resource_snapshot or {},
    )
    return ExpressionLabService(robot=robot, pair_watcher=pair_watcher), runtime


def test_service_owns_expression_lifecycle() -> None:
    service, runtime = make_service()

    started = service.start(
        preset="standby",
        style="watcher",
        gaze_x=0.25,
        gaze_y=-0.1,
        openness=0.8,
        spacing=0.85,
        scale=1.0,
        scale_x=2.0,
        scale_y=2.0,
        stroke=1.0,
        roundness=1.0,
        left_openness=1.0,
        right_openness=1.0,
        tilt_deg=4,
        left_tilt_deg=0,
        right_tilt_deg=0,
        tag="none",
        accessory="halo",
        accessory_scale=1.25,
        accessory_x=0.2,
        accessory_y=-0.15,
        accessory_rotation_deg=18,
        auto_blink=True,
        blink_interval_ms=3600,
        blink_duration_ms=200,
        color="#A1F03C",
        sphere_strength=0.68,
        transition_ms=180,
    )
    updated = service.update(gaze_x=-0.4, openness=0.65, transition_ms=120)
    stopped = service.stop()

    assert started["active"] is True
    assert updated["parameters"]["gaze_x"] == -0.4
    assert stopped["active"] is False
    assert runtime.calls == [
        (
            "start",
            {
                "preset": "standby",
                "style": "watcher",
                "gaze_x": 0.25,
                "gaze_y": -0.1,
                "openness": 0.8,
                "spacing": 0.85,
                "scale": 1.0,
                "scale_x": 2.0,
                "scale_y": 2.0,
                "stroke": 1.0,
                "roundness": 1.0,
                "left_openness": 1.0,
                "right_openness": 1.0,
                "tilt_deg": 4,
                "left_tilt_deg": 0,
                "right_tilt_deg": 0,
                "tag": "none",
                "accessory": "halo",
                "accessory_scale": 1.25,
                "accessory_x": 0.2,
                "accessory_y": -0.15,
                "accessory_rotation_deg": 18,
                "auto_blink": True,
                "blink_interval_ms": 3600,
                "blink_duration_ms": 200,
                "color": "#A1F03C",
                "sphere_strength": 0.68,
                "transition_ms": 180,
            },
        ),
        ("update", {"gaze_x": -0.4, "openness": 0.65, "transition_ms": 120}),
        ("stop", {}),
    ]


def test_update_requires_an_active_expression() -> None:
    service, _ = make_service()

    try:
        service.update(gaze_x=0.2)
    except RuntimeError as error:
        assert str(error) == "expression runtime is not active"
    else:
        raise AssertionError("inactive update should fail")


def test_status_distinguishes_offline_and_incompatible_devices() -> None:
    offline, _ = make_service(connected=False)
    incompatible, _ = make_service(expression_supported=False)

    assert offline.status()["device_connected"] is False
    assert offline.status()["expression_supported"] is False
    assert incompatible.status()["device_connected"] is True
    assert incompatible.status()["expression_supported"] is False

    for service, message in (
        (offline, "not connected"),
        (incompatible, "does not support"),
    ):
        try:
            service.start(preset="standby", style="watcher")
        except RuntimeError as error:
            assert message in str(error)
        else:
            raise AssertionError("start should require a compatible connected Watcher")


def test_status_tolerates_one_probe_timeout_but_discards_repeated_stale_capabilities() -> None:
    runtime = FakeExpressionRuntime()

    def fail_refresh(*, timeout: float) -> dict[str, object]:
        assert timeout <= 0.5
        raise TimeoutError("device channel did not reply")

    service = ExpressionLabService(
        robot=SimpleNamespace(
            expression_runtime=runtime,
            capabilities=("expression.runtime.v1", "expression.runtime.v2"),
            device_info={"model": "Watcher"},
            resource_snapshot={},
            refresh_device_info=fail_refresh,
        )
    )
    service._active = True

    first = service.status()
    second = service.status()
    third = service.status()

    assert first["device_connected"] is True
    assert first["expression_supported"] is True
    assert first["active"] is True
    assert second["device_connected"] is True
    assert second["active"] is True
    assert third["device_connected"] is False
    assert third["expression_supported"] is False
    assert third["active"] is False


def test_successful_device_probe_resets_the_consecutive_timeout_guard() -> None:
    runtime = FakeExpressionRuntime()
    outcomes = iter((TimeoutError("slow"), None, TimeoutError("slow"), TimeoutError("slow")))

    def refresh(*, timeout: float) -> dict[str, object]:
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome
        return {"model": "Watcher"}

    service = ExpressionLabService(
        robot=SimpleNamespace(
            expression_runtime=runtime,
            capabilities=("expression.runtime.v1", "expression.runtime.v2"),
            device_info={"model": "Watcher"},
            resource_snapshot={},
            refresh_device_info=refresh,
        )
    )

    assert service.status()["device_connected"] is True
    assert service.status()["device_connected"] is True
    assert service.status()["device_connected"] is True
    assert service.status()["device_connected"] is True


def test_status_exposes_device_expression_performance_without_fabricating_samples() -> None:
    service, _ = make_service(
        resource_snapshot={
            "animation": {
                "sample_valid": True,
                "measured_fps_x100": 1985,
                "target_fps_x100": 2000,
                "draw_ewma_us": 11340,
                "frame_buffer_bytes": 84872,
            },
            "memory": {"psram": {"free_bytes": 6_301_696}},
        }
    )

    performance = service.status()["performance"]

    assert performance == {
        "sample_valid": True,
        "measured_fps": 19.85,
        "target_fps": 20.0,
        "draw_ms": 11.34,
        "frame_buffer_bytes": 84872,
        "psram_free_bytes": 6_301_696,
    }


def test_failed_restart_does_not_report_stale_active_state() -> None:
    runtime = FailingStartRuntime()
    service = ExpressionLabService(
        robot=SimpleNamespace(
            expression_runtime=runtime,
            capabilities=("expression.runtime.v1", "expression.runtime.v2"),
            device_info={"model": "Watcher"},
        )
    )
    service.start(preset="standby", style="watcher")
    runtime.fail = True

    try:
        service.start(preset="standby", style="watcher")
    except RuntimeError as error:
        assert str(error) == "device rejected start"
    else:
        raise AssertionError("failed start should propagate")

    assert service.status()["active"] is False
    assert runtime.calls[-2][0] == "start"
    assert runtime.calls[-1] == ("stop", {})


def test_web_api_returns_structured_json_for_sdk_command_rejection(tmp_path: Path) -> None:
    class RejectedRuntime(FakeExpressionRuntime):
        def start(self, preset: str, **parameters: object) -> None:
            super().start(preset, **parameters)
            raise CommandError("ctrl.expression.runtime.start", "invalid_state")

    runtime = RejectedRuntime()
    service = ExpressionLabService(
        robot=SimpleNamespace(
            expression_runtime=runtime,
            capabilities=("expression.runtime.v1", "expression.runtime.v2"),
            device_info={"model": "Watcher"},
            resource_snapshot={},
        )
    )
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<h1>lab</h1>", encoding="utf-8")

    with TestClient(create_web_app(service, web_root=web_root), raise_server_exceptions=False) as client:
        response = client.post("/api/expression/start", json={"preset": "standby"})

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": "ctrl.expression.runtime.start rejected: invalid_state"
    }
    assert runtime.calls[-1] == ("stop", {})


def test_web_api_validates_ranges_and_stops_on_shutdown(tmp_path: Path) -> None:
    service, runtime = make_service()
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<h1>lab</h1>", encoding="utf-8")
    app = create_web_app(service, web_root=web_root)

    with TestClient(app) as client:
        response = client.post(
            "/api/expression/start",
            json={"preset": "thinking", "style": "watcher_focus", "tag": "question"},
        )
        assert response.status_code == 200
        assert client.get("/api/status").json()["active"] is True
        invalid = client.post("/api/expression/update", json={"gaze_x": 2.0})
        assert invalid.status_code == 422

    assert runtime.calls[-1] == ("stop", {})


def test_web_pairing_accepts_only_a_six_digit_code(tmp_path: Path) -> None:
    pairing_codes: list[str] = []
    service, _ = make_service(connected=False, pair_watcher=pairing_codes.append)
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<h1>lab</h1>", encoding="utf-8")

    with TestClient(create_web_app(service, web_root=web_root)) as client:
        invalid = client.post("/api/pair", json={"pairing_code": "58471"})
        paired = client.post("/api/pair", json={"pairing_code": "584711"})

    assert invalid.status_code == 422
    assert paired.status_code == 200
    assert pairing_codes == ["584711"]
    assert "584711" not in str(paired.json())


def test_web_pairing_reports_management_errors_without_echoing_code(tmp_path: Path) -> None:
    def fail_pairing(_pairing_code: str) -> None:
        raise RuntimeError("pairing code 584711 was rejected")

    service, _ = make_service(connected=False, pair_watcher=fail_pairing)
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<h1>lab</h1>", encoding="utf-8")

    with TestClient(create_web_app(service, web_root=web_root)) as client:
        response = client.post("/api/pair", json={"pairing_code": "584711"})

    assert response.status_code == 409
    assert response.json() == {"detail": "pairing code <pairing-code> was rejected"}
    assert "584711" not in response.text


def test_web_index_uses_prefix_safe_relative_asset_urls() -> None:
    service, _ = make_service()
    web_root = Path(__file__).resolve().parents[1] / "web"

    with TestClient(create_web_app(service, web_root=web_root)) as client:
        index = client.get("/")
        stylesheet = client.get("/styles.css")
        script = client.get("/app.js")

    assert 'href="./styles.css?v=expression-lab-15"' in index.text
    assert 'src="./app.js?v=expression-lab-15"' in index.text
    assert 'id="connectionGuide"' in index.text
    assert 'id="pairingForm"' in index.text
    assert 'id="pairingCode"' in index.text
    assert 'inputmode="numeric"' in index.text
    assert 'maxlength="6"' in index.text
    assert index.headers["cache-control"] == "no-store"
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "no-store"
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert script.headers["cache-control"] == "no-store"
    assert 'fetch("./api/status"' in script.text
    assert 'response.headers.get("content-type")' in script.text
    assert "await response.text()" in script.text
    assert "statusInitialized" in script.text
    assert "正在连接 SDK" in script.text
    assert "window.setInterval(refreshConnectionStatus, 1500)" in script.text
    assert "snapshot.device_connected" in script.text
    assert "snapshot.expression_supported" in script.text
    assert 'api("./api/pair", { pairing_code: pairingCode })' in script.text
    assert 'scale_x: Number(controls.scaleX.value)' in script.text
    assert 'scale: Number(controls.scale.value)' in script.text
    assert 'accessory: controls.accessory.value' in script.text
    assert 'id="accessory"' in index.text
    assert 'accessory_scale: Number(controls.accessoryScale.value)' in script.text
    assert 'accessory_x: Number(controls.accessoryX.value)' in script.text
    assert 'accessory_y: Number(controls.accessoryY.value)' in script.text
    assert 'accessory_rotation_deg: Number(controls.accessoryRotation.value)' in script.text
    assert 'id="accessoryScale"' in index.text
    assert 'id="accessoryX"' in index.text
    assert 'id="accessoryY"' in index.text
    assert 'id="accessoryRotation"' in index.text
    assert 'id="eyeControlsModule"' in index.text
    assert 'id="accessoryControlsModule"' in index.text
    assert "眼睛自定义" in index.text
    assert "标签与装饰" in index.text
    assert "仅作用于头部装饰" in index.text
    assert 'byId("accessoryControlsModule").dataset.active' in script.text
    assert 'controls.accessoryScale.disabled = !hasAccessory' in script.text
    assert 'id="deviceFps"' in index.text
    assert 'color: controls.eyeColor.value.toUpperCase()' in script.text
    assert 'id="sphereEnabled"' in index.text
    assert 'id="sphereStrength"' in index.text
    assert "function buildSphereMap(strength)" in script.text
    assert "presentFrame(p.sphere_strength)" in script.text
    assert "sphere_strength: controls.sphereEnabled.checked" in script.text
    assert 'id="pointerTracking"' in index.text
    assert 'id="pointerTrackingState"' in index.text
    assert 'id="pointerGain"' in index.text
    assert 'id="pointerGainValue"' in index.text
    assert 'canvas.addEventListener("pointermove", updatePointerTarget)' in script.text
    assert 'canvas.addEventListener("pointerleave", releasePointerTarget)' in script.text
    assert "function updatePointerMotion(dt)" in script.text
    assert "function maybeSyncPointerGaze(now)" in script.text
    assert "transition_ms: pointerTiming.transitionMs" in script.text
    assert "Math.exp(-dt / POINTER_SMOOTHING_MS)" in script.text
    assert "POINTER_GAZE_GAIN_DEFAULT" in script.text
    assert "POINTER_SPHERE_TRANSITION_MS" in script.text
    assert "controls.pointerGain.value" in script.text
    assert "const GAZE_TRAVEL_PIXELS = 32" in script.text
    assert "p.gaze_x * GAZE_TRAVEL_PIXELS" in script.text
    assert "p.gaze_y * GAZE_TRAVEL_PIXELS" in script.text
    assert "sendExpressionUpdate" in script.text
    assert "在 Watcher 打开 Desktop Link" in script.text
    assert "打开 Python SDK" not in script.text
    assert '"/api/' not in script.text
    assert "for (let y = 39; y <= 60; y += 1)" in script.text
    assert "tagCircle(164 * 2, 69 * 2, 3 * 2)" in script.text
    assert "tagCircle(166 * 2, 48 * 2, 6 * 2)" in script.text
    assert "tagCircle(176 * 2, 48 * 2, 6 * 2)" in script.text
    assert "for (let row = 0; row < 12; row += 1)" in script.text
    assert "intentActive" in script.text
    assert "scheduleExpressionResume" in script.text
    assert "连接恢复，代码表情已重新同步" in script.text
