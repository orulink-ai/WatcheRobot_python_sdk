from __future__ import annotations

import asyncio

from watcherobot.desktop_session import DesktopRobotSession


def test_correlated_device_event_completes_pending_command() -> None:
    async def exercise() -> None:
        session = DesktopRobotSession("ws://127.0.0.1:1")
        future = asyncio.get_running_loop().create_future()
        session._pending["storage-1"] = future
        message = {
            "type": "evt.resource.storage",
            "code": 0,
            "data": {"command_id": "storage-1", "total_bytes": 1024},
        }

        assert session._resolve_pending(message) is True
        assert await future == message

    asyncio.run(exercise())
