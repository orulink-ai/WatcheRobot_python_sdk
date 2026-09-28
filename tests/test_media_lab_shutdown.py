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


def test_shutdown_signals_workload_before_waiting_for_http_requests(monkeypatch):
    root = Path(__file__).parents[1] / "examples/sdk_media_lab"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location("lab_shutdown_order", root / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def scenario():
        server = SimpleNamespace(should_exit=False)
        app = SimpleNamespace(shutdown_requested=True)
        cancelled = asyncio.Event()
        service = SimpleNamespace(request_shutdown=cancelled.set)
        async def serve():
            await cancelled.wait()
            while not server.should_exit:
                await asyncio.sleep(.01)
        task = asyncio.create_task(serve())
        try:
            await asyncio.wait_for(module.wait_for_server(app, server, task, service=service), 1)
            assert cancelled.is_set() and task.done()
        finally:
            cancelled.set()
            server.should_exit = True
            await task
    asyncio.run(scenario())


def test_active_http_pressure_test_drains_after_daemon_shutdown(monkeypatch, tmp_path):
    import socket
    import threading
    import httpx
    import uvicorn
    from tests.test_sdk_media_lab import _load_service_module, _service
    from tests.test_sdk_media_lab_concurrency import _stress_robot

    root = Path(__file__).parents[1] / "examples/sdk_media_lab"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location("lab_active_shutdown", root / "app.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    service_module = _load_service_module()
    robot = _stress_robot()
    playing = threading.Event()
    stops = []
    def wait(timeout):
        playing.set()
        threading.Event().wait(min(timeout, .1))
        raise TimeoutError("still playing")
    robot.audio.play_file = lambda path: SimpleNamespace(wait=wait)
    robot.audio.stop = lambda: stops.append("speaker")
    robot.expression_runtime.stop = lambda: stops.append("ui")
    service = _service(service_module, tmp_path, robot)

    async def scenario():
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(service_module.create_web_app(service, web_root=root / "web"), log_level="error"))
        control = SimpleNamespace(shutdown_requested=False)
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        waiter = asyncio.create_task(entry.wait_for_server(control, server, serving, service=service))
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            try:
                async def ready():
                    while not server.started:
                        await asyncio.sleep(.01)
                await asyncio.wait_for(ready(), 2)
                request = asyncio.create_task(client.post(f"http://127.0.0.1:{port}/api/concurrency/start", json={"duration":30}))
                assert await asyncio.to_thread(playing.wait, 2)
                control.shutdown_requested = True
                await asyncio.wait_for(asyncio.shield(waiter), 2.5)
                report = (await request).json()
                assert report["cancelled"] is True
                assert set(stops) == {"speaker", "ui"}
                assert not service.status()["resource_owners"]
            finally:
                control.shutdown_requested = True
                service.request_shutdown()
                await asyncio.wait_for(asyncio.shield(waiter), 3)
                listener.close()
    asyncio.run(scenario())
