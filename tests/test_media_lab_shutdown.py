import asyncio
import threading
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize("callback", ["sample_scenario", "maintain"])
def test_lifespan_drains_background_threads_before_returning(tmp_path, callback):
    from tests.test_sdk_media_lab import _load_service_module, _service
    module = _load_service_module()
    service = _service(module, tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(timeout=3.0)
        finished.set()

    setattr(service, callback, blocked)
    app = module.create_web_app(service, web_root=Path(__file__).parents[1] / "examples/sdk_media_lab/web")

    async def scenario():
        exit_requested = asyncio.Event()

        async def run():
            async with app.router.lifespan_context(app):
                await exit_requested.wait()

        running = asyncio.create_task(run())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            exit_requested.set()
            await asyncio.sleep(.1)
            assert not running.done(), "SDK context could close while the worker still runs"
            release.set()
            await asyncio.wait_for(asyncio.shield(running), 2)
            assert finished.is_set()
        finally:
            release.set()
            exit_requested.set()
            await running

    asyncio.run(scenario())


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


def test_http_photo_drains_before_procedural_cleanup_and_transport_close(monkeypatch, tmp_path):
    import socket
    import threading
    import httpx
    import uvicorn
    from tests.test_sdk_media_lab import _load_service_module, _service, _procedural_robot

    root = Path(__file__).parents[1] / "examples/sdk_media_lab"
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location("lab_photo_shutdown", root / "app.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    module = _load_service_module()
    robot = _procedural_robot()
    service = _service(module, tmp_path, robot)
    service.start_procedural()
    entered, release = threading.Event(), threading.Event()
    capture = robot.camera.capture
    follow = robot.expression_runtime.set_audio_follow
    order = []

    def blocked_capture(**kwargs):
        entered.set()
        assert release.wait(timeout=5.0)
        result = capture(**kwargs)
        order.append("photo")
        return result

    def confirmed_follow(enabled):
        follow(enabled)
        if not enabled:
            order.append("confirmed-stop")

    robot.camera.capture = blocked_capture
    robot.expression_runtime.set_audio_follow = confirmed_follow

    async def scenario():
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(module.create_web_app(service, web_root=root / "web"), log_level="error"))
        control = SimpleNamespace(shutdown_requested=False)
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        waiter = asyncio.create_task(entry.wait_for_server(control, server, serving, service=service))
        try:
            async def ready():
                while not server.started:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(ready(), 2)
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                request = asyncio.create_task(client.post(f"http://127.0.0.1:{port}/api/actions/capture-photo"))
                assert await asyncio.to_thread(entered.wait, 2)
                control.shutdown_requested = True
                await asyncio.sleep(.15)
                assert not waiter.done() and not order
                release.set()
                assert (await request).status_code == 200
                await asyncio.wait_for(asyncio.shield(waiter), 3)
                assert service._procedural_lease is None
                assert not service.status()["resource_owners"]
                # main() leaves ApplicationContext only after wait_for_server.
                order.append("transport-close")
                assert order == ["photo", "confirmed-stop", "transport-close"]
        finally:
            release.set()
            control.shutdown_requested = True
            await asyncio.wait_for(asyncio.shield(waiter), 3)
            listener.close()
    asyncio.run(scenario())
