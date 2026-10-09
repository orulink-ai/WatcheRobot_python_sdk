"""Daemon-managed entry point for the Speaking Motion Lab Application."""
import asyncio
from pathlib import Path

from watcherobot.application import ApplicationContext

from application import serve_application


async def main() -> None:
    async with ApplicationContext.from_environment() as context:
        await serve_application(context, Path(__file__).resolve().parent / "web")


if __name__ == "__main__":
    asyncio.run(main())
