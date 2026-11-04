import asyncio

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from watcherobot.runtime.daemon.connections.registry import ExternalConnectionRegistry, ExternalClientRole


def test_closed_device_send_does_not_abort_sender_or_other_devices():
    async def scenario():
        registry = ExternalConnectionRegistry()
        attached = asyncio.Queue()

        async def handler(websocket):
            connection = registry.add(websocket)
            registry.declare_role(connection, role="hardware")
            await attached.put(connection)
            # Retain identity to reproduce the close/remove race.
            await websocket.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with connect(url) as stale, connect(url) as active:
                first = await attached.get()
                await attached.get()
                await stale.close()
                await first.websocket.wait_closed()
                frame = b"opaque business frame"
                assert await registry.send_to_role(ExternalClientRole.DEVICE, frame) == 1
                assert await asyncio.wait_for(active.recv(), 1) == frame
                registry.remove(first.websocket)
                assert await registry.send_to_role(ExternalClientRole.DEVICE, frame) == 1
                assert await asyncio.wait_for(active.recv(), 1) == frame

    asyncio.run(scenario())
