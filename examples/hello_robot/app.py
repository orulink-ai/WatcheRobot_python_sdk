"""Play one happy behavior as a minimal WatcheRobot Hello World."""

import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("device=%s", app.robot.device_info)
        job = await asyncio.to_thread(
            app.robot.behavior.play,
            "happy",
            repeat=1,
        )
        await asyncio.to_thread(job.wait, 20.0)
        app.logger.info("Hello, WatcheRobot! The happy behavior completed.")


if __name__ == "__main__":
    asyncio.run(main())
