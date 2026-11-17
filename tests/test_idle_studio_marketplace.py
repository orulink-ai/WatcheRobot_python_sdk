import asyncio
import hashlib
import io
import re
import sys

import pytest

from watcherobot.idle_studio import official


def test_public_download_is_atomic_and_needs_no_github_cli(tmp_path, monkeypatch):
    payload = b"public release archive"
    monkeypatch.setattr(official, "ARCHIVE_SHA256", hashlib.sha256(payload).hexdigest())
    calls = []
    def download(request, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(payload)
    monkeypatch.setattr(official, "urlopen", download)
    resource = official.OfficialResources(tmp_path)
    assert resource._archive().read_bytes() == payload
    assert calls[0][0].startswith("https://github.com/orulink-ai/WatcheRobot_sd/releases/download/")
    assert len(calls) == 1
    resource._archive()
    assert len(calls) == 1
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("failure", ["checksum", "network", "oversize"])
def test_failed_download_never_installs_partial_archive(tmp_path, monkeypatch, failure):
    def download(*args, **kwargs):
        if failure == "network":
            raise OSError("offline")
        return io.BytesIO(b"incorrect payload")
    monkeypatch.setattr(official, "urlopen", download)
    if failure == "oversize":
        monkeypatch.setattr(official, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError):
        official.OfficialResources(tmp_path)._archive()
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("resources.tar.gz"))


def test_export_is_self_contained_and_validates_as_application(tmp_path):
    from watcherobot.idle_studio.marketplace import export_application
    from watcherobot.distribution.check import check_application
    target = export_application(tmp_path / "app")
    result = check_application(target, watcherobot_version="0.1.14")
    assert result.app_id == "com.orulink.idle_studio"
    assert result.supported_host_platforms == ("windows",)
    assert (target / "idle_studio/web/vendor/watcherobot-studio.glb").is_file()
    assert (target / "idle_studio/web/vendor/THREE-LICENSE.txt").is_file()
    assert not list(target.rglob("__pycache__"))
    assert not (target / "idle_studio/web/design-proposal.html").exists()
    assert "watcherobot.idle_studio" not in (target / "app.py").read_text()
    with pytest.raises(FileExistsError):
        export_application(target)


def test_managed_server_shutdown_and_desktop_frames_are_drained():
    from watcherobot.idle_studio.managed import wait_for_shutdown
    class Desktop:
        count = 0
        async def receive(self, **kwargs):
            self.count += 1
            await asyncio.sleep(0.001)
    class App:
        desktop = Desktop()
        @property
        def shutdown_requested(self):
            return self.desktop.count >= 3
    class Server:
        should_exit = False
    async def scenario():
        app, server = App(), Server()
        async def serve():
            while not server.should_exit:
                await asyncio.sleep(0.001)
        task = asyncio.create_task(serve())
        await wait_for_shutdown(app, server, task)
        assert server.should_exit and task.done()
        assert app.desktop.count >= 3
    asyncio.run(scenario())


def test_exported_app_runs_under_real_daemon_manager_and_stops(tmp_path, monkeypatch):
    import httpx
    from watcherobot.idle_studio.marketplace import export_application
    from watcherobot.runtime.daemon.application.runtime import ApplicationRuntimeManager
    from watcherobot.runtime.daemon.application.launcher import ApplicationLauncher
    from watcherobot.runtime.daemon.application.logging import ApplicationLogService
    from watcherobot.runtime.daemon.application.session import ApplicationChannel
    from pathlib import Path

    target = export_application(tmp_path / "standalone")
    monkeypatch.setenv("WATCHER_IDLE_STUDIO_NO_BROWSER", "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    messages = []
    async def scenario():
        manager = ApplicationRuntimeManager(
            application_dir=target, current_app="com.orulink.idle_studio",
            application_launcher=ApplicationLauncher(
                managed_app_root=Path(sys.executable).parent,
                bundled_resource_root=tmp_path / "resources",
            ), startup_timeout=15,
            log_service=ApplicationLogService(
                log_dir=tmp_path / "logs", desktop_forwarder=messages.append,
            ),
        )
        manager.select_application(target, launcher_kind="python", launcher_executable=Path(sys.executable))
        url = None
        try:
            await manager.start()
            for _ in range(300):
                match = re.search(r"Watcher Idle Studio: (http://127\.0\.0\.1:\d+)", " ".join(messages))
                if match:
                    url = match.group(1)
                    break
                await asyncio.sleep(0.02)
            assert url, messages
            async with httpx.AsyncClient(trust_env=False) as client:
                assert (await client.get(url)).status_code == 200
                assert (await client.get(url + "/api/ports")).status_code == 200
                assert (await client.get(url + "/vendor/watcherobot-studio.glb")).status_code == 200
                assert (await client.post(url + "/api/validate", json={})).status_code in (409, 422)
            await manager.bridge.send_to_application(ApplicationChannel.DESKTOP, '{"type":"ctrl.microphone.open"}')
        finally:
            await manager.stop()
        assert manager.process_id is None
        assert manager.last_exit_code == 0
        async with httpx.AsyncClient(trust_env=False) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(url)
    asyncio.run(scenario())
