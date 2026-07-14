"""拍摄一张 JPEG，并保存到仓库的 artifacts 目录。"""

from __future__ import annotations

import os
from pathlib import Path

from watcherobot import WatcheRobot


OUTPUT_FILE = Path(__file__).resolve().parents[1] / "artifacts" / "camera.jpg"


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "请输入机器人 SDK Control App 显示的六位配对码："
    )
    input("即将调用摄像头并把照片保存到本机；确认环境允许拍摄后按回车继续。")

    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        image = robot.camera.capture(timeout=10.0)

    if not image.data.startswith(b"\xff\xd8"):
        raise RuntimeError("机器人返回的内容不是 JPEG")
    saved = image.save(OUTPUT_FILE)
    print(f"照片已保存：{saved}（{len(image.data)} bytes）")


if __name__ == "__main__":
    main()
