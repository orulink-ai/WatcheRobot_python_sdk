"""Minimal example: connect to the robot and play a factory Behavior."""

from __future__ import annotations

import os

from watcherobot import WatcheRobot


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "Six-digit code shown by SDK Control App / 请输入机器人显示的六位配对码："
    )

    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        print("Connected / 已连接：", robot.device_info)
        robot.behavior.play("happy", repeat=1).wait(timeout=20.0)
        print("happy Behavior completed / 播放完成。")


if __name__ == "__main__":
    main()
