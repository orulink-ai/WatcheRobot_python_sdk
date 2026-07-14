"""录制五秒机器人麦克风音频，并保存为标准 WAV。"""

from __future__ import annotations

import os
from pathlib import Path

from watcherobot import WatcheRobot


OUTPUT_FILE = Path(__file__).resolve().parents[1] / "artifacts" / "microphone.wav"


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "请输入机器人 SDK Control App 显示的六位配对码："
    )
    input("即将录音五秒并保存到本机；确认环境允许录音后按回车继续。")

    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        recording = robot.microphone.record(duration=5.0, timeout=8.0)

    saved = recording.save(OUTPUT_FILE)
    print(
        f"录音已保存：{saved}（{recording.duration_seconds:.3f} 秒，"
        f"dropped_frames={recording.dropped_frames}）"
    )


if __name__ == "__main__":
    main()
