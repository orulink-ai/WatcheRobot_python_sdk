import os

from watcherobot import WatcheRobot


def main() -> None:
    pairing_code = os.environ.get("WATCHEROBOT_PAIRING_CODE") or input("请输入机器人屏幕上的六位配对码：")
    with WatcheRobot.connect(pairing_code=pairing_code.strip()) as robot:
        print(robot.device_info)
        robot.behavior.play("happy").wait(timeout=5)
        robot.motion.set_target(pan_deg=105)
        robot.lights.set_color("#4DA3FF", brightness=0.7)


if __name__ == "__main__":
    main()
