from watcherobot import WatcheRobot


def main() -> None:
    with WatcheRobot.connect(pairing_code="123456") as robot:
        print(robot.device_info)
        robot.behavior.play("greeting").wait(timeout=5)
        robot.motion.set_target(pan_deg=105)
        robot.lights.set_color("#4DA3FF", brightness=0.7)


if __name__ == "__main__":
    main()

