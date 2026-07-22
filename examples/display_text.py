from __future__ import annotations

import os

from watcherobot import WatcheRobot


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "Six-digit code shown by SDK Control App / 请输入机器人显示的六位配对码："
    )

    with WatcheRobot.connect(pairing_code=pairing_code.strip()) as robot:
        robot.display.show_text(
            "你好，WatcheRobot！",
            size=24,
            color="#FFFFFF",
            background="#000000",
            align="center",
        )
        input("Press Enter to clear the screen / 按回车键清除屏幕……")
        robot.display.clear()


if __name__ == "__main__":
    main()
