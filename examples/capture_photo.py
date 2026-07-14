"""Capture one JPEG and save it under the repository artifacts directory."""

from __future__ import annotations

import os
from pathlib import Path

from watcherobot import WatcheRobot


OUTPUT_FILE = Path(__file__).resolve().parents[1] / "artifacts" / "camera.jpg"


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input(
        "Six-digit code shown by SDK Control App / 请输入机器人显示的六位配对码："
    )
    input(
        "The camera will capture and save a local photo. Press Enter after consent. / "
        "即将拍照并保存到本机；确认现场人员知情后按回车继续。"
    )

    with WatcheRobot.connect(pairing_code=pairing_code.strip(), timeout=30.0) as robot:
        image = robot.camera.capture(timeout=10.0)

    if not image.data.startswith(b"\xff\xd8"):
        raise RuntimeError("robot did not return a JPEG / 机器人返回的内容不是 JPEG")
    saved = image.save(OUTPUT_FILE)
    print(f"Photo saved / 照片已保存：{saved} ({len(image.data)} bytes)")


if __name__ == "__main__":
    main()
