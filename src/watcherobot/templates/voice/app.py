"""固定入口：在项目目录执行 watcherobot app run。"""
import asyncio
from pathlib import Path

from application.voice import main


if __name__ == '__main__':
    asyncio.run(main(Path(__file__).resolve().parent))
