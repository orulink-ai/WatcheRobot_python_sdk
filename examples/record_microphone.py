"""Record five seconds from the robot microphone and save a standard WAV."""

from __future__ import annotations

import os
from pathlib import Path

from watcherobot import WatcheRobot


OUTPUT_FILE = Path(__file__).resolve().parents[1] / "artifacts" / "microphone.wav"


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "Six-digit code shown by SDK Control App / 请输入机器人显示的六位配对码："
    )
    input(
        "The microphone will record five seconds. Press Enter after consent. / "
        "即将录音五秒并保存到本机；确认现场人员知情后按回车继续。"
    )

    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        recording = robot.microphone.record(duration=5.0, timeout=8.0)

    saved = recording.save(OUTPUT_FILE)
    print(
        f"Recording saved / 录音已保存：{saved} "
        f"({recording.duration_seconds:.3f}s, dropped_frames={recording.dropped_frames})"
    )


if __name__ == "__main__":
    main()
