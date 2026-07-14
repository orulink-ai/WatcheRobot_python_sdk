"""把电脑上的 PCM WAV 文件传给机器人播放。"""

from __future__ import annotations

import os
from pathlib import Path

from watcherobot import WatcheRobot


SAMPLE_AUDIO = Path(__file__).with_name("assets") / "sample_speech.wav"


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "请输入机器人 SDK Control App 显示的六位配对码："
    )

    print(f"即将传输并播放：{SAMPLE_AUDIO.resolve()}")
    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        robot.audio.play_file(SAMPLE_AUDIO).wait(timeout=30.0)
    print("设备端播放完成。")


if __name__ == "__main__":
    main()
