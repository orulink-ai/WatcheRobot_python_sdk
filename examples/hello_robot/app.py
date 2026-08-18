"""Play a welcome behavior, then continuously showcase demo behaviors."""

import asyncio
import random

from watcherobot.application import ApplicationContext


# Firmware behavior-state IDs, not raw expression or animation resource IDs.
DEMO_BEHAVIORS = (
    "smile",
    "shock",
    "sunglasses",
    "speechless",
    "concentration",
    "get",
    "query",
    "fondle_love",
)
DEMO_BEHAVIOR_SECONDS = 4.0


def _shuffled_demo_behaviors(previous_behavior: str | None) -> list[str]:
    behaviors = list(DEMO_BEHAVIORS)
    random.shuffle(behaviors)
    if (
        previous_behavior is not None
        and len(behaviors) > 1
        and behaviors[0] == previous_behavior
    ):
        behaviors[0], behaviors[1] = behaviors[1], behaviors[0]
    return behaviors


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("device=%s", app.robot.device_info)
        await asyncio.to_thread(
            app.robot.behavior.play("happy", repeat=1).wait,
            20.0,
        )
        app.logger.info("Welcome complete; starting the behavior showcase.")
        previous_behavior = None
        while True:
            for behavior_id in _shuffled_demo_behaviors(previous_behavior):
                await asyncio.to_thread(
                    app.robot.behavior.play,
                    behavior_id,
                    repeat=1,
                )
                app.logger.info("Playing demo behavior: %s", behavior_id)
                previous_behavior = behavior_id
                await asyncio.sleep(DEMO_BEHAVIOR_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
