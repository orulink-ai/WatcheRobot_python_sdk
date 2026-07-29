"""Capture one JPEG as a managed Application."""

import asyncio
from pathlib import Path

from watcherobot.application import ApplicationContext


OUTPUT_FILE = Path(__file__).resolve().parent / "artifacts" / "camera.jpg"


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        image = await asyncio.to_thread(app.robot.camera.capture, timeout=10.0)
        saved = image.save(OUTPUT_FILE)
        app.logger.info("photo saved: %s", saved)


asyncio.run(main())
