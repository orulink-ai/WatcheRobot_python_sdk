"""最小可运行示例：连接机器人并播放一个出厂 Behavior。"""

from __future__ import annotations

import os

from watcherobot import WatcheRobot


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "请输入机器人 SDK Control App 显示的六位配对码："
    )

    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        print("已连接：", robot.device_info)
        robot.behavior.play("happy", repeat=1).wait(timeout=20.0)
        print("happy Behavior 播放完成。")


if __name__ == "__main__":
    main()
