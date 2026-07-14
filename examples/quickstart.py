"""直接调用主要 SDK 能力的交互式快速开始。"""

from __future__ import annotations

import os
from pathlib import Path

from watcherobot import WatcheRobot


EXAMPLE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = EXAMPLE_DIR.parent / "artifacts" / "quickstart"
SAMPLE_AUDIO = EXAMPLE_DIR / "assets" / "sample_speech.wav"


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "请输入机器人 SDK Control App 显示的六位配对码："
    )

    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        print("\n设备信息：", robot.device_info)
        print("设备能力：", ", ".join(robot.capabilities))

        print("\n[1/7] 播放多轨 Behavior")
        robot.behavior.play("happy", repeat=1).wait(timeout=20.0)

        print("\n[2/7] 直接播放动画")
        robot.animation.play("smile").wait(timeout=10.0)

        print("\n[3/7] 设置灯光")
        robot.lights.set_color("#4DA3FF", brightness=0.5)

        input("\n[4/7] 即将移动机器人；确认周围没有障碍物后按回车继续。")
        robot.motion.move_to(
            pan_deg=100,
            tilt_deg=120,
            duration_ms=1000,
        ).wait(timeout=10.0)

        print("\n[5/7] 把电脑 WAV 传给机器人播放")
        robot.audio.play_file(SAMPLE_AUDIO).wait(timeout=30.0)

        input("\n[6/7] 即将拍照；确认现场人员知情后按回车继续。")
        image = robot.camera.capture(timeout=10.0)
        image_path = image.save(OUTPUT_DIR / "camera.jpg")
        print(f"照片已保存：{image_path.resolve()}")

        input("\n[7/7] 即将录音五秒；确认现场人员知情后按回车继续。")
        recording = robot.microphone.record(duration=5.0, timeout=8.0)
        recording_path = recording.save(OUTPUT_DIR / "microphone.wav")
        print(
            f"录音已保存：{recording_path.resolve()}，"
            f"时长={recording.duration_seconds:.3f}s，"
            f"dropped_frames={recording.dropped_frames}"
        )

        robot.lights.off()

    print("\nQuickstart 全部执行完成。")


if __name__ == "__main__":
    main()
