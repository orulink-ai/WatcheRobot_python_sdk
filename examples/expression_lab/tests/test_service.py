import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from watcherobot.errors import CommandError

from service import ExpressionLabService, ExpressionStartRequest, create_web_app


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


def test_expression_start_defaults_to_flat_rendering() -> None:
    request = ExpressionStartRequest(preset="standby")

    assert request.sphere_strength == 0.0
    assert request.left_upper_lid_y == -80
    assert request.right_upper_lid_y == -80
    assert request.left_lower_lid_y == 80
    assert request.right_lower_lid_y == 80


def test_expression_lab_rejects_the_retired_pixel_accessory_payload() -> None:
    with pytest.raises(ValidationError):
        ExpressionStartRequest(
            preset="standby",
            accessory="custom_pixel",
            custom_accessory_mask="80" + "00" * 325,
            custom_accessory_layer="back",
        )


def test_expression_request_accepts_the_bounded_vector_accessory_payload() -> None:
    path = "010108020032001400460014"

    request = ExpressionStartRequest(
        preset="standby",
        accessory="custom_vector",
        custom_vector_path=path,
        custom_accessory_layer="front",
    )

    assert request.custom_vector_path == path


@pytest.mark.parametrize("path", ("010", "00" * 795, "0101zz"))
def test_expression_lab_rejects_unbounded_or_malformed_vector_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        ExpressionStartRequest(
            preset="standby",
            accessory="custom_vector",
            custom_vector_path=path,
        )


def make_service(
    *,
    connected: bool = True,
    expression_supported: bool = True,
    pair_watcher=None,
    resource_snapshot=None,
) -> tuple[ExpressionLabService, FakeExpressionRuntime]:
    runtime = FakeExpressionRuntime()
    capabilities = ("expression.runtime.v1", "expression.runtime.v2", "expression.runtime.v3") if expression_supported else ("animation",)
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
        left_upper_lid_y=-65,
        left_upper_lid_rotation_deg=0,
        right_upper_lid_y=-65,
        right_upper_lid_rotation_deg=0,
        left_lower_lid_y=65,
        left_lower_lid_rotation_deg=0,
        right_lower_lid_y=65,
        right_lower_lid_rotation_deg=0,
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
                "left_upper_lid_y": -65,
                "left_upper_lid_rotation_deg": 0,
                "right_upper_lid_y": -65,
                "right_upper_lid_rotation_deg": 0,
                "left_lower_lid_y": 65,
                "left_lower_lid_rotation_deg": 0,
                "right_lower_lid_y": 65,
                "right_lower_lid_rotation_deg": 0,
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
            capabilities=("expression.runtime.v1", "expression.runtime.v2", "expression.runtime.v3"),
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


def test_stop_releases_a_runtime_that_may_still_be_active_after_disconnect() -> None:
    runtime = FakeExpressionRuntime()

    def fail_refresh(*, timeout: float) -> dict[str, object]:
        raise TimeoutError("device channel did not reply")

    service = ExpressionLabService(
        robot=SimpleNamespace(
            expression_runtime=runtime,
            capabilities=("expression.runtime.v3",),
            device_info={"model": "Watcher"},
            resource_snapshot={},
            refresh_device_info=fail_refresh,
        )
    )
    service._active = True
    service._runtime_claimed = True

    service.status()
    service.status()
    service.status()
    assert service.status()["active"] is False

    service.stop()

    assert runtime.calls == [("stop", {})]


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
            capabilities=("expression.runtime.v1", "expression.runtime.v2", "expression.runtime.v3"),
            device_info={"model": "Watcher"},
            resource_snapshot={},
            refresh_device_info=refresh,
        )
    )

    assert service.status()["device_connected"] is True
    assert service.status()["device_connected"] is True
    assert service.status()["device_connected"] is True
    assert service.status()["device_connected"] is True


def test_expression_commands_do_not_repeat_the_expensive_device_probe() -> None:
    runtime = FakeExpressionRuntime()
    probe_count = 0

    def refresh(*, timeout: float) -> dict[str, object]:
        nonlocal probe_count
        assert timeout <= 0.5
        probe_count += 1
        return {"model": "Watcher"}

    service = ExpressionLabService(
        robot=SimpleNamespace(
            expression_runtime=runtime,
            capabilities=("expression.runtime.v3", "expression.vector_accessory.v1"),
            device_info={"model": "Watcher"},
            resource_snapshot={},
            refresh_device_info=refresh,
        )
    )

    service.start(preset="standby", style="watcher")
    service.update(gaze_x=0.25)
    service.stop()

    assert probe_count == 1


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
            capabilities=("expression.runtime.v1", "expression.runtime.v2", "expression.runtime.v3"),
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
            capabilities=("expression.runtime.v1", "expression.runtime.v2", "expression.runtime.v3"),
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


def test_web_exposes_verified_firmware_bundle_for_incompatible_device(tmp_path: Path) -> None:
    service, _ = make_service(expression_supported=False)
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<h1>lab</h1>", encoding="utf-8")
    firmware_root = tmp_path / "firmware"
    firmware_root.mkdir()
    payload = b"firmware-package"
    filename = "Watcher-Expression-Lab-Firmware-v0.1.1-esp32s3.zip"
    digest = hashlib.sha256(payload).hexdigest()
    (firmware_root / filename).write_bytes(payload)
    (firmware_root / "firmware-package.json").write_text(
        json.dumps(
            {
                "app_id": "com.orulink.expression_lab",
                "app_version": "0.1.1",
                "filename": filename,
                "size_bytes": len(payload),
                "sha256": digest,
                "required_capability": "expression.runtime.v3",
                "source": {"pull_request": 199, "commit": "c" * 40},
            }
        ),
        encoding="utf-8",
    )

    with TestClient(
        create_web_app(service, web_root=web_root, firmware_root=firmware_root)
    ) as client:
        status = client.get("/api/status").json()["firmware_update"]
        downloaded = client.get("/api/firmware/download")

    assert status == {
        "required": True,
        "available": True,
        "filename": filename,
        "size_bytes": len(payload),
        "sha256": digest,
        "required_capability": "expression.runtime.v3",
        "source_pull_request": 199,
        "source_commit": "c" * 40,
        "download_url": "./api/firmware/download",
    }
    assert downloaded.status_code == 200
    assert downloaded.content == payload
    assert downloaded.headers["content-type"] == "application/zip"
    assert filename in downloaded.headers["content-disposition"]


def test_web_refuses_tampered_firmware_bundle(tmp_path: Path) -> None:
    service, _ = make_service(expression_supported=False)
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<h1>lab</h1>", encoding="utf-8")
    firmware_root = tmp_path / "firmware"
    firmware_root.mkdir()
    filename = "firmware.zip"
    (firmware_root / filename).write_bytes(b"tampered")
    (firmware_root / "firmware-package.json").write_text(
        json.dumps(
            {
                "app_id": "com.orulink.expression_lab",
                "app_version": "0.1.1",
                "filename": filename,
                "size_bytes": 8,
                "sha256": "0" * 64,
                "required_capability": "expression.runtime.v3",
                "source": {"pull_request": 199, "commit": "c" * 40},
            }
        ),
        encoding="utf-8",
    )

    with TestClient(
        create_web_app(service, web_root=web_root, firmware_root=firmware_root)
    ) as client:
        status = client.get("/api/status").json()["firmware_update"]
        downloaded = client.get("/api/firmware/download")

    assert status == {"required": True, "available": False}
    assert downloaded.status_code == 404


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
        vector_path = client.get("/vector-path.js")
        script = client.get("/app.js")

    assert 'href="./styles.css?v=expression-lab-28"' in index.text
    assert 'src="./vector-path.js?v=expression-lab-28"' in index.text
    assert 'src="./app.js?v=expression-lab-28"' in index.text
    assert 'id="firmwareUpdate"' in index.text
    assert 'id="firmwareDownload"' in index.text
    assert 'id="connectionGuide"' in index.text
    assert 'id="pairingForm"' in index.text
    assert 'id="pairingCode"' in index.text
    assert 'inputmode="numeric"' in index.text
    assert 'maxlength="6"' in index.text
    assert index.headers["cache-control"] == "no-store"
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "no-store"
    assert ".controls { grid-column: 2 / -1; }" in stylesheet.text
    assert ".inspector { grid-column: 1 / -1; }" in stylesheet.text
    assert ".stage { position: sticky; top: 16px; }" in stylesheet.text
    assert vector_path.status_code == 200
    assert "function normalize(strokes)" in vector_path.text
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
    assert "snapshot.firmware_update" in script.text
    assert 'href="./api/firmware/download"' in index.text
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
    assert 'id="sphereEnabled"' not in index.text
    assert 'id="sphereStrength"' not in index.text
    assert "function buildSphereMap(strength)" not in script.text
    assert "willReadFrequently" not in script.text
    assert "sphere_strength: 0" in script.text
    assert "displayCtx.drawImage(flatCanvas, 0, 0)" in script.text
    for reset_id in ("resetEyeDefaults", "resetEyelidDefaults", "resetAccessoryDefaults"):
        assert f'id="{reset_id}"' in index.text
        assert f'byId("{reset_id}").addEventListener("click"' in script.text
    assert "const eyeControlDefaults" in script.text
    assert "const accessoryControlDefaults" in script.text
    assert "function applyControlDefaults(defaults)" in script.text
    assert 'id="pointerTracking"' in index.text
    assert 'id="pointerTrackingState"' in index.text
    assert 'id="pointerGain"' in index.text
    assert 'id="pointerGainValue"' in index.text
    assert 'canvas.addEventListener("pointermove", updatePointerTarget)' in script.text
    assert 'canvas.addEventListener("pointerleave", releasePointerTarget)' in script.text
    assert "function updatePointerMotion(dt)" in script.text
    assert "function maybeSyncPointerGaze(now)" in script.text
    assert "transition_ms: POINTER_FLAT_TRANSITION_MS" in script.text
    assert "Math.exp(-dt / POINTER_SMOOTHING_MS)" in script.text
    assert "POINTER_GAZE_GAIN_DEFAULT" in script.text
    assert "POINTER_SPHERE_TRANSITION_MS" not in script.text
    assert "controls.pointerGain.value" in script.text
    assert "const GAZE_TRAVEL_PIXELS = 32" in script.text
    assert "p.gaze_x * GAZE_TRAVEL_PIXELS" in script.text
    assert "p.gaze_y * GAZE_TRAVEL_PIXELS" in script.text
    for lid_id in (
        "leftUpperLidY",
        "leftUpperLidRotation",
        "rightUpperLidY",
        "rightUpperLidRotation",
        "leftLowerLidY",
        "leftLowerLidRotation",
        "rightLowerLidY",
        "rightLowerLidRotation",
    ):
        assert f'id="{lid_id}"' in index.text
        assert f'id="{lid_id}Number"' in index.text
        assert f'aria-label="{lid_id} 精确值"' in index.text
    assert 'class="range-with-number"' in index.text
    assert "const rangeNumberEditors" in script.text
    assert "function initializeRangeNumberEditors()" in script.text
    assert "function syncRangeValueFromEditor(range, editor)" in script.text
    assert "document.querySelectorAll('input[type=\"range\"]')" in script.text
    assert 'data-eye-shape=' not in index.text
    assert "const eyeShapeDefaults" not in script.text
    assert 'id="eyelidPresetName"' in index.text
    assert 'id="saveEyelidPreset"' in index.text
    assert 'id="eyelidPresetList"' in index.text
    assert 'id="eyelidPresetEmpty"' in index.text
    assert "const EYELID_PRESET_STORAGE_KEY" in script.text
    assert "const EYELID_PRESET_COOKIE_KEY" in script.text
    assert "document.cookie" in script.text
    assert "function saveCurrentEyelidPreset()" in script.text
    assert "function applyEyelidPreset(presetId)" in script.text
    assert "function deleteEyelidPreset(presetId)" in script.text
    assert "drawEyelidMasks" in script.text
    assert "const LID_MASK_HALF_WIDTH_PIXELS = 112" in script.text
    assert "context.rect(clipLeft, 0, clipRight - clipLeft, canvas.height)" in script.text
    assert 'const eyeCanvas = document.createElement("canvas")' in script.text
    assert 'eyeCtx.globalCompositeOperation = "destination-out"' in script.text
    assert "ctx.drawImage(eyeCanvas, 0, 0)" in script.text
    assert "sendExpressionUpdate" in script.text
    assert "在 Watcher 打开 Desktop Link" in script.text
    assert "打开 Python SDK" not in script.text
    assert '"/api/' not in script.text
    assert "const TAG_SVG_PATHS" in script.text
    assert "M500.382 0.006c-177.646" in script.text
    assert "M533.504 268.288q33.792-41.984" in script.text
    assert "M480 179.2c12.8 6.4" in script.text
    assert "const TAG_SVG_LAYOUTS" in script.text
    assert "function drawSvgTagPath(tag, color, gazeOffsetX = 0, gazeOffsetY = 0)" in script.text
    assert "new Path2D(pathData)" in script.text
    assert "tagCircle(" not in script.text
    assert "const SECONDARY_GAZE_FOLLOW = 0.5" in script.text
    assert "drawAccessory(p.accessory, \"back\", state.phase, p, secondaryGazeX, secondaryGazeY)" in script.text
    assert "drawAccessory(p.accessory, \"front\", state.phase, p, secondaryGazeX, secondaryGazeY)" in script.text
    assert "drawTag(p.tag, p.color, secondaryGazeX, secondaryGazeY)" in script.text
    assert 'value="custom_pixel"' not in index.text
    assert 'id="pixelAccessoryCanvas"' not in index.text
    assert "PIXEL_ACCESSORY_" not in script.text
    assert "pixelAccessory" not in script.text
    assert "custom_accessory_mask" not in script.text
    assert 'id="vectorAccessoryCanvas"' in index.text
    assert 'id="vectorBrush"' in index.text
    assert 'id="vectorEraser"' in index.text
    assert 'id="vectorUndo"' in index.text
    assert 'id="vectorRedo"' in index.text
    assert 'id="saveVectorAccessory"' in index.text
    assert "const MAX_STROKES = 12" in vector_path.text
    assert "const MAX_POINTS = 192" in vector_path.text
    assert "while (total > MAX_POINTS)" in vector_path.text
    assert "VectorPath.MAX_STROKES" in script.text
    assert "function encodeVectorAccessoryPath()" in script.text
    assert "function drawCustomVectorAccessory" in script.text
    assert "custom_vector_path: encodeVectorAccessoryPath()" in script.text
    assert "intentActive" in script.text
    assert "scheduleExpressionResume" in script.text
    assert "连接恢复，代码表情已重新同步" in script.text
