"""Exercise common domain APIs from a managed Application."""

import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("capabilities=%s", app.robot.capabilities)
        behavior = await asyncio.to_thread(
            app.robot.behavior.play,
            "happy",
            repeat=1,
        )
        await asyncio.to_thread(behavior.wait, 20.0)
        await asyncio.to_thread(app.robot.display.show_text, "Hello\nWatcheRobot")
        await asyncio.sleep(2.0)
        await asyncio.to_thread(app.robot.display.clear)
        await asyncio.to_thread(
            app.robot.lights.set_color,
            "#4DA3FF",
            brightness=0.5,
        )
        motion = await asyncio.to_thread(
            app.robot.motion.move_to,
            pan_deg=100,
            tilt_deg=120,
            duration_ms=1000,
        )
        await asyncio.to_thread(motion.wait, 10.0)
        await asyncio.to_thread(app.robot.lights.off)


asyncio.run(main())
