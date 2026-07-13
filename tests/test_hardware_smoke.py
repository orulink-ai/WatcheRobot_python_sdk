from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_smoke_module():
    path = Path(__file__).parents[1] / "examples" / "hardware_smoke.py"
    spec = importlib.util.spec_from_file_location("hardware_smoke", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Job:
    def __init__(self, calls: list[tuple], name: str) -> None:
        self.calls = calls
        self.name = name

    def wait(self, timeout: float):
        self.calls.append((f"{self.name}.wait", timeout))
        return self


class _Domain:
    def __init__(self, calls: list[tuple], name: str) -> None:
        self.calls = calls
        self.name = name

    def play(self, resource_id: str, **kwargs):
        self.calls.append((f"{self.name}.play", resource_id, kwargs))
        return _Job(self.calls, self.name)


class _Lights:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def set_color(self, color: str, *, brightness: float):
        self.calls.append(("lights.set_color", color, brightness))

    def off(self):
        self.calls.append(("lights.off",))


class _Motion:
    def __init__(self, calls: list[tuple]) -> None:
        self.calls = calls

    def move_to(self, **kwargs):
        self.calls.append(("motion.move_to", kwargs))
        return _Job(self.calls, "motion")

    def stop(self):
        self.calls.append(("motion.stop",))


class _Robot:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.device_info = {"device_id": "test"}
        self.capabilities = ("behavior", "animation", "motion", "audio", "light")
        self.lights = _Lights(self.calls)
        self.animation = _Domain(self.calls, "animation")
        self.audio = _Domain(self.calls, "audio")
        self.behavior = _Domain(self.calls, "behavior")
        self.motion = _Motion(self.calls)


class _FakeSerial:
    def __init__(self) -> None:
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.dtr = None
        self.rts = None
        self.writes: list[bytes] = []
        self.lines = [b"I boot ready\n", b"W SDK_CONTROL_APP: SDK_SMOKE pairing_code=471642\n"]
        self.closed = False

    def open(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    def close(self) -> None:
        self.closed = True


def test_auto_pair_opens_sdk_app_and_reads_debug_pairing_code() -> None:
    module = _load_smoke_module()
    serial_port = _FakeSerial()

    pairing_code = module._read_pairing_code_from_serial(
        "COM5", timeout=1.0, serial_factory=lambda: serial_port
    )

    assert pairing_code == "471642"
    assert serial_port.port == "COM5"
    assert b"debug.app.open sdk.control.app\n" in serial_port.writes
    assert b"debug.sdk.pairing\n" in serial_port.writes
    assert serial_port.closed


def test_default_smoke_uses_installed_happy_resources_and_skips_motion(tmp_path: Path) -> None:
    module = _load_smoke_module()
    robot = _Robot()

    failures = module.run_smoke(robot, module.SmokeOptions(), tmp_path)

    assert failures == []
    assert ("animation.play", "happy", {}) in robot.calls
    assert ("audio.play", "happy", {}) in robot.calls
    assert ("behavior.play", "happy", {"repeat": 1}) in robot.calls
    assert not any(call[0] == "motion.move_to" for call in robot.calls)
    assert robot.calls[-1] == ("lights.off",)


def test_motion_requires_explicit_option_and_is_stopped_after_test(tmp_path: Path) -> None:
    module = _load_smoke_module()
    robot = _Robot()
    options = module.SmokeOptions(with_motion=True, motion_pan_deg=101, motion_tilt_deg=121)

    failures = module.run_smoke(robot, options, tmp_path)

    assert failures == []
    assert ("motion.move_to", {"pan_deg": 101, "tilt_deg": 121, "duration": 1.0}) in robot.calls
    assert robot.calls[-1] == ("motion.stop",)
