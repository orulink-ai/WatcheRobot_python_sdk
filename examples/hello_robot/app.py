"""Play one built-in behavior as a managed Application."""

import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("device=%s", app.robot.device_info)
        await asyncio.to_thread(
            app.robot.behavior.play("happy", repeat=1).wait,
            20.0,
        )


asyncio.run(main())
