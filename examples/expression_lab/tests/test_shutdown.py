import asyncio
from types import SimpleNamespace

from app import wait_for_server


def test_daemon_shutdown_drains_http_server():
    async def scenario():
        server = SimpleNamespace(should_exit=False)
        context = SimpleNamespace(shutdown_requested=True)
        finished = []

        async def serve():
            while not server.should_exit:
                await asyncio.sleep(0)
            finished.append(True)

        task = asyncio.create_task(serve())
        await asyncio.wait_for(wait_for_server(context, server, task), timeout=1)
        assert finished == [True]

    asyncio.run(scenario())
