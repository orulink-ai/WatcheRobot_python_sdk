"""Application 固定入口，由 watcherobot app run 启动。"""

import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("Application started.")
        # 在这里编写应用逻辑，使用 app.robot 和 app.desktop 与设备、桌面交互。
        while not app.shutdown_requested:
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
