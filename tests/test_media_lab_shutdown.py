import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_daemon_shutdown_stops_http_server(monkeypatch):
    root = Path(__file__).parents[1] / 'examples/sdk_media_lab'
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location('lab_shutdown_app', root / 'app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def scenario():
        server = SimpleNamespace(should_exit=False)
        app = SimpleNamespace(shutdown_requested=True)
        async def serve():
            while not server.should_exit:
                await asyncio.sleep(.01)
        task = asyncio.create_task(serve())
        await asyncio.wait_for(module.wait_for_server(app, server, task), 1)
        assert server.should_exit and task.done()
    asyncio.run(scenario())
