"""Interactive hardware smoke test for a WatcheRobot on the local network."""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from watcherobot import WatcheRobot


_PAIRING_CODE_LOG = re.compile(r"SDK_SMOKE pairing_code=(\d{6})")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_AUDIO_FILE = _REPOSITORY_ROOT / "examples" / "assets" / "sample_speech.wav"
_DEFAULT_OUTPUT_DIR = _REPOSITORY_ROOT / "artifacts" / "hardware-smoke"


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
            raise RuntimeError("自动配对需要安装 watcherobot[hardware]") from error
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
        # Re-enter through launcher so an already-active SDK app also receives
        # a clean close/open lifecycle, fresh pairing code, and new discovery task.
        serial_port.write(b"debug.app.open launcher\n")
        time.sleep(0.25)
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
    raise RuntimeError("未读取到调试配对码；请烧录启用 WATCHER_DEBUG_CLI 的固件")


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
    job_timeout: float = 20.0
    with_motion: bool = False
    with_microphone: bool = False
    with_camera: bool = False
    interactive: bool = False
    audio_file: Path = _DEFAULT_AUDIO_FILE
    motion_pan_deg: int = 100
    motion_tilt_deg: int = 120
    motion_duration: float = 1.0
    microphone_seconds: float = 5.0


def _run_step(
    name: str,
    action: Callable[[], None],
    failures: list[str],
    *,
    confirm: Callable[[str], str] | None = None,
) -> None:
    print(f"\n[RUN ] {name}")
    try:
        action()
    except Exception as error:  # Keep independent hardware checks running.
        failures.append(f"{name}: {error}")
        print(f"[FAIL] {name}: {error}")
        return
    if confirm is not None:
        answer = confirm(
            f"[CHECK] 请确认“{name}”效果；按回车进入下一项，输入 f 标记失败："
        ).strip().lower()
        if answer == "f":
            failures.append(f"{name}: 人工确认未通过")
            print(f"[FAIL] {name}: 人工确认未通过")
            return
    print(f"[PASS] {name}")


def _record_microphone(robot: WatcheRobot, output_path: Path, duration: float) -> None:
    if duration <= 0:
        raise ValueError("录音时长必须大于零")
    frames: list[bytes] = []
    print(f"       请靠近机器人说话，将录制 {duration:g} 秒……")
    with robot.microphone.open() as microphone:
        first_frame = microphone.read(timeout=max(2.0, duration))
        frames.append(first_frame.data)
        audio_format = microphone.format
        bytes_per_second = (
            audio_format.sample_rate_hz
            * audio_format.channels
            * audio_format.sample_width_bytes
        )
        target_bytes = max(audio_format.sample_width_bytes, round(bytes_per_second * duration))
        recorded_bytes = len(first_frame.data)
        deadline = time.monotonic() + duration + 2.0
        last_countdown = 0
        while recorded_bytes < target_bytes and time.monotonic() < deadline:
            remaining_audio = (target_bytes - recorded_bytes) / bytes_per_second
            countdown = max(1, math.ceil(remaining_audio))
            if countdown != last_countdown:
                print(f"       正在录音，还剩 {countdown} 秒……")
                last_countdown = countdown
            try:
                frame = microphone.read(timeout=min(1.0, max(0.1, deadline - time.monotonic())))
            except TimeoutError:
                continue
            frames.append(frame.data)
            recorded_bytes += len(frame.data)
        dropped_frames = microphone.dropped_frames

    pcm = b"".join(frames)[:target_bytes]
    if len(pcm) < target_bytes:
        raise RuntimeError(
            f"麦克风数据不足：期望 {target_bytes} 字节，实际 {len(pcm)} 字节"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(audio_format.channels)
        wav_file.setsampwidth(audio_format.sample_width_bytes)
        wav_file.setframerate(audio_format.sample_rate_hz)
        wav_file.writeframes(pcm)
    print(
        f"       录音已保存：{output_path.resolve()} "
        f"(frames={len(frames)}, dropped={dropped_frames})"
    )


def _capture_camera(robot: WatcheRobot, output_path: Path) -> None:
    image = robot.camera.capture(timeout=10.0)
    if not image.data.startswith(b"\xff\xd8"):
        raise RuntimeError("摄像头返回的内容不是 JPEG")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image.data)
    print(f"       照片已保存：{output_path.resolve()} ({len(image.data)} bytes)")


def run_smoke(
    robot: WatcheRobot,
    options: SmokeOptions,
    output_dir: Path,
    *,
    confirm: Callable[[str], str] | None = None,
) -> list[str]:
    failures: list[str] = []
    step_confirm = (confirm or input) if options.interactive else None
    print("设备信息：", robot.device_info)
    print("设备能力：", ", ".join(robot.capabilities))

    _run_step(
        "灯光设置为蓝色",
        lambda: robot.lights.set_color("#4DA3FF", brightness=0.5),
        failures,
        confirm=step_confirm,
    )
    _run_step(
        "播放 happy 动画",
        lambda: robot.animation.play("happy").wait(timeout=options.job_timeout),
        failures,
        confirm=step_confirm,
    )
    _run_step(
        f"把电脑音频传给机器人播放（{options.audio_file.name}）",
        lambda: robot.audio.play_file(options.audio_file).wait(timeout=options.job_timeout),
        failures,
        confirm=step_confirm,
    )
    _run_step(
        "播放 happy Behavior",
        lambda: robot.behavior.play("happy", repeat=1).wait(timeout=options.job_timeout),
        failures,
        confirm=step_confirm,
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
            confirm=step_confirm,
        )
    if options.with_camera:
        _run_step(
            "拍摄单张 JPEG",
            lambda: _capture_camera(robot, output_dir / "camera.jpg"),
            failures,
            confirm=step_confirm,
        )
    if options.with_microphone:
        _run_step(
            f"录制麦克风 PCM（{options.microphone_seconds:g} 秒）",
            lambda: _record_microphone(
                robot,
                output_dir / "microphone.wav",
                options.microphone_seconds,
            ),
            failures,
            confirm=step_confirm,
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
    parser = argparse.ArgumentParser(description="WatcheRobot Python SDK 实机冒烟测试")
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
    parser.add_argument("--job-timeout", type=float, default=20.0)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--audio-file", type=Path, default=_DEFAULT_AUDIO_FILE)
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="不等待人工按回车，供 Codex/CI 自动化使用",
    )
    parser.add_argument("--all", action="store_true", help="启用动作、麦克风和摄像头测试")
    parser.add_argument("--with-motion", action="store_true", help="启用安全小范围动作测试")
    parser.add_argument("--with-microphone", action="store_true", help="录制并保存 WAV")
    parser.add_argument("--with-camera", action="store_true", help="拍摄并保存单张 JPEG")
    parser.add_argument("--motion-pan", type=int, default=100)
    parser.add_argument("--motion-tilt", type=int, default=120)
    parser.add_argument("--microphone-seconds", type=float, default=5.0)
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
        interactive=not args.non_interactive,
        audio_file=args.audio_file,
        motion_pan_deg=args.motion_pan,
        motion_tilt_deg=args.motion_tilt,
        microphone_seconds=args.microphone_seconds,
    )

    print("正在启动 UDP Discovery 与 WebSocket 网关，请保持机器人停留在 SDK Control App……")
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
        print(f"[FAIL] 连接或配对失败：{error}")
        return 2

    if failures:
        print("\n冒烟测试存在失败项：")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\n全部已启用的冒烟测试通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
