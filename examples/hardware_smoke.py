"""Interactive hardware smoke test for a WatcheRobot on the local network."""

from __future__ import annotations

import argparse
import re
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from watcherobot import WatcheRobot


_PAIRING_CODE_LOG = re.compile(r"SDK_SMOKE pairing_code=(\d{6})")


def _open_pairing_serial(
    port: str,
    *,
    timeout: float = 30.0,
    serial_factory: Callable[[], object] | None = None,
) -> tuple[str, object]:
    if serial_factory is None:
        try:
            import serial
        except ImportError as error:
            raise RuntimeError("auto pairing requires 'pip install watcherobot[hardware]'") from error
        serial_factory = serial.Serial

    serial_port = serial_factory()
    serial_port.port = port
    serial_port.baudrate = 115200
    serial_port.timeout = 0.2
    serial_port.dtr = False
    serial_port.rts = False
    deadline = time.monotonic() + timeout
    next_open_request = 0.0
    try:
        serial_port.open()
        serial_port.reset_input_buffer()
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_open_request:
                serial_port.write(b"debug.app.open sdk.control.app\n")
                serial_port.write(b"debug.sdk.pairing\n")
                next_open_request = now + 2.0
            raw_line = serial_port.readline()
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                print(f"[ESP32] {line}")
            match = _PAIRING_CODE_LOG.search(line)
            if match is not None:
                return match.group(1), serial_port
    except BaseException:
        serial_port.close()
        raise
    serial_port.close()
    raise RuntimeError(
        "no debug pairing code received; flash a firmware with CONFIG_WATCHER_DEBUG_CLI_ENABLE=y"
    )


def _read_pairing_code_from_serial(
    port: str,
    *,
    timeout: float = 30.0,
    serial_factory: Callable[[], object] | None = None,
) -> str:
    pairing_code, serial_port = _open_pairing_serial(
        port,
        timeout=timeout,
        serial_factory=serial_factory,
    )
    serial_port.close()
    return pairing_code


@dataclass(frozen=True)
class SmokeOptions:
    job_timeout: float = 10.0
    with_motion: bool = False
    with_microphone: bool = False
    with_camera: bool = False
    motion_pan_deg: int = 100
    motion_tilt_deg: int = 120
    motion_duration: float = 1.0
    microphone_seconds: float = 1.0


def _run_step(name: str, action: Callable[[], None], failures: list[str]) -> None:
    print(f"[RUN ] {name}")
    try:
        action()
    except Exception as error:  # The script must report all independent hardware failures.
        failures.append(f"{name}: {error}")
        print(f"[FAIL] {name}: {error}")
    else:
        print(f"[PASS] {name}")


def _record_microphone(robot: WatcheRobot, output_path: Path, duration: float) -> None:
    frames: list[bytes] = []
    with robot.microphone.open() as microphone:
        # Device-side codec startup is asynchronous. Start the recording window
        # after the first PCM frame instead of consuming it with startup jitter.
        first_frame = microphone.read(timeout=max(2.0, duration))
        frames.append(first_frame.data)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            frame = microphone.read(timeout=remaining)
            frames.append(frame.data)
        audio_format = microphone.format
        dropped_frames = microphone.dropped_frames

    if not frames:
        raise RuntimeError("microphone returned no PCM frames")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(audio_format.channels)
        wav_file.setsampwidth(audio_format.sample_width_bytes)
        wav_file.setframerate(audio_format.sample_rate_hz)
        wav_file.writeframes(b"".join(frames))
    print(f"       microphone={output_path} frames={len(frames)} dropped={dropped_frames}")


def _capture_camera(robot: WatcheRobot, output_path: Path) -> None:
    image = robot.camera.capture(timeout=10.0)
    if not image.data.startswith(b"\xff\xd8"):
        raise RuntimeError("camera payload is not a JPEG image")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image.data)
    print(f"       camera={output_path} bytes={len(image.data)}")


def run_smoke(robot: WatcheRobot, options: SmokeOptions, output_dir: Path) -> list[str]:
    failures: list[str] = []
    print("设备信息:", robot.device_info)
    print("设备能力:", ", ".join(robot.capabilities))

    _run_step(
        "灯光设为蓝色",
        lambda: robot.lights.set_color("#4DA3FF", brightness=0.5),
        failures,
    )
    _run_step(
        "播放 happy 动画",
        lambda: robot.animation.play("happy").wait(timeout=options.job_timeout),
        failures,
    )
    _run_step(
        "播放 happy 音效",
        lambda: robot.audio.play("happy").wait(timeout=options.job_timeout),
        failures,
    )
    _run_step(
        "播放 happy Behavior",
        lambda: robot.behavior.play("happy", repeat=1).wait(timeout=options.job_timeout),
        failures,
    )

    if options.with_motion:
        _run_step(
            f"动作移动到 pan={options.motion_pan_deg}, tilt={options.motion_tilt_deg}",
            lambda: robot.motion.move_to(
                pan_deg=options.motion_pan_deg,
                tilt_deg=options.motion_tilt_deg,
                duration=options.motion_duration,
            ).wait(timeout=options.job_timeout),
            failures,
        )
    if options.with_camera:
        _run_step(
            "拍摄单张 JPEG",
            lambda: _capture_camera(robot, output_dir / "camera.jpg"),
            failures,
        )
    if options.with_microphone:
        _run_step(
            "采集麦克风 PCM",
            lambda: _record_microphone(
                robot,
                output_dir / "microphone.wav",
                options.microphone_seconds,
            ),
            failures,
        )

    try:
        robot.lights.off()
    except Exception as error:
        failures.append(f"关闭灯光: {error}")
    if options.with_motion:
        try:
            robot.motion.stop()
        except Exception as error:
            failures.append(f"停止动作: {error}")
    return failures


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WatcheRobot Python SDK 实机 smoke 测试")
    parser.add_argument("--pairing-code", help="机器人屏幕显示的六位临时配对码")
    parser.add_argument(
        "--auto-pair-port",
        metavar="COMx",
        help="通过 Debug CLI 打开 SDK App 并从串口读取临时配对码",
    )
    parser.add_argument("--auto-pair-timeout", type=float, default=30.0)
    parser.add_argument("--host", default="0.0.0.0", help="网关监听地址，默认监听全部网卡")
    parser.add_argument("--discovery-port", type=int, default=37021)
    parser.add_argument("--websocket-port", type=int, default=8766)
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--job-timeout", type=float, default=10.0)
    parser.add_argument("--output-dir", type=Path, default=Path("watcherobot-smoke-output"))
    parser.add_argument("--all", action="store_true", help="启用动作、麦克风和摄像头测试")
    parser.add_argument("--with-motion", action="store_true", help="启用安全小范围动作测试")
    parser.add_argument("--with-microphone", action="store_true", help="录制约一秒 WAV")
    parser.add_argument("--with-camera", action="store_true", help="拍摄并保存单张 JPEG")
    parser.add_argument("--motion-pan", type=int, default=100)
    parser.add_argument("--motion-tilt", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.pairing_code:
        pairing_code = args.pairing_code
    elif args.auto_pair_port:
        pairing_code = _read_pairing_code_from_serial(
            args.auto_pair_port,
            timeout=args.auto_pair_timeout,
        )
        print(f"已从 {args.auto_pair_port} 获取 Debug 配对码。")
    else:
        pairing_code = input("请输入机器人屏幕上的六位配对码：").strip()
    options = SmokeOptions(
        job_timeout=args.job_timeout,
        with_motion=args.all or args.with_motion,
        with_microphone=args.all or args.with_microphone,
        with_camera=args.all or args.with_camera,
        motion_pan_deg=args.motion_pan,
        motion_tilt_deg=args.motion_tilt,
    )

    print("正在启动 UDP Discovery 与 WebSocket 网关，请保持机器人停留在 Python SDK App……")
    try:
        with WatcheRobot.connect(
            pairing_code=pairing_code,
            discovery_port=args.discovery_port,
            websocket_port=args.websocket_port,
            timeout=args.connect_timeout,
            host=args.host,
        ) as robot:
            failures = run_smoke(robot, options, args.output_dir)
    except Exception as error:
        print(f"[FAIL] 连接或配对失败: {error}")
        return 2

    if failures:
        print("\nSmoke 测试存在失败项：")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\n全部已启用的 Smoke 测试通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
