from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import websockets
from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.runtime import DaemonRuntime
from watcherobot.runtime.daemon.application.launcher import ApplicationLaunchError
from watcherobot.runtime.daemon.application.session import (
    ApplicationChannel,
    ApplicationState,
    SessionOccupiedError,
)
from tests.runtime.pairing_helpers import connect_runtime_hardware


RELAY_APPLICATION = """
import asyncio
import os

from websockets.asyncio.client import connect


async def relay(websocket):
    async for frame in websocket:
        await websocket.send(frame)


async def main():
    async with (
        connect(os.environ["WATCHER_APP_DESKTOP_WS_URL"]) as desktop,
        connect(os.environ["WATCHER_APP_DEVICE_WS_URL"]) as device,
    ):
        await asyncio.gather(relay(desktop), relay(device))


asyncio.run(main())
"""

EXITING_APPLICATION = """
import asyncio
import os

from websockets.asyncio.client import connect


async def main():
    async with (
        connect(os.environ["WATCHER_APP_DESKTOP_WS_URL"]),
        connect(os.environ["WATCHER_APP_DEVICE_WS_URL"]),
    ):
        await asyncio.sleep(0.1)


asyncio.run(main())
"""

PYTHON_IDENTITY_APPLICATION = """
import asyncio
import inspect
import os
from pathlib import Path
import sys

from websockets.asyncio.client import connect


async def main():
    Path(os.environ["PYTHON_IDENTITY_FILE"]).write_text(
        sys.executable,
        encoding="utf-8",
    )
    connect_options = (
        {"proxy": None}
        if "proxy" in inspect.signature(connect).parameters
        else {}
    )
    async with (
        connect(
            os.environ["WATCHER_APP_DESKTOP_WS_URL"],
            **connect_options,
        ),
        connect(
            os.environ["WATCHER_APP_DEVICE_WS_URL"],
            **connect_options,
        ),
    ):
        await asyncio.Event().wait()


asyncio.run(main())
"""


def _write_relay_application(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "test_app",
                "name": "Routing Test Application",
                "version": "1.0.0",
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text(RELAY_APPLICATION, encoding="utf-8")


def _write_exiting_application(root: Path) -> None:
    _write_relay_application(root)
    root.joinpath("app.py").write_text(EXITING_APPLICATION, encoding="utf-8")


def _select_python_application(
    runtime: DaemonRuntime,
    application_dir: Path,
) -> None:
    runtime.select_application(
        str(application_dir.resolve()),
        "python",
        str(Path(sys.executable)),
    )


def _create_test_python_environment(root: Path) -> Path:
    base_executable = Path(getattr(sys, "_base_executable", sys.executable))
    subprocess.run(
        [str(base_executable), "-m", "venv", "--without-pip", str(root)],
        check=True,
    )
    if sys.platform == "win32":
        executable = root / "Scripts" / "python.exe"
        site_packages = root / "Lib" / "site-packages"
    else:
        executable = root / "bin" / "python"
        site_packages = (
            root
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )
    site_packages.joinpath("watcher-test-dependencies.pth").write_text(
        str(Path(websockets.__file__).resolve().parent.parent),
        encoding="utf-8",
    )
    return executable


def _expected_application_python(executable: Path) -> Path:
    if sys.platform == "win32":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw.resolve()
    return executable


def test_same_daemon_switches_between_two_real_python_environments(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        managed_root = tmp_path / "application-store"
        app_a_dir = managed_root / "apps" / "app_a" / "source"
        app_b_dir = managed_root / "apps" / "app_b" / "source"
        _write_relay_application(app_a_dir)
        _write_relay_application(app_b_dir)
        app_a_dir.joinpath("app.json").write_text(
            app_a_dir.joinpath("app.json")
            .read_text(encoding="utf-8")
            .replace('"test_app"', '"app_a"'),
            encoding="utf-8",
        )
        app_b_dir.joinpath("app.json").write_text(
            app_b_dir.joinpath("app.json")
            .read_text(encoding="utf-8")
            .replace('"test_app"', '"app_b"'),
            encoding="utf-8",
        )
        app_a_dir.joinpath("app.py").write_text(
            PYTHON_IDENTITY_APPLICATION,
            encoding="utf-8",
        )
        app_b_dir.joinpath("app.py").write_text(
            PYTHON_IDENTITY_APPLICATION,
            encoding="utf-8",
        )
        python_a = _create_test_python_environment(
            managed_root / "apps" / "app_a" / ".venv"
        )
        python_b = _create_test_python_environment(
            managed_root / "apps" / "app_b" / ".venv"
        )
        runtime = DaemonRuntime(
            application_dir=tmp_path / "unselected",
            current_app="unselected",
            managed_app_root=managed_root,
            bundled_resource_root=tmp_path / "bundled-resources",
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
            preview_udp_port=0,
        )
        daemon_pid = os.getpid()
        identity_a = tmp_path / "python-a.txt"
        identity_b = tmp_path / "python-b.txt"

        await runtime.start()
        try:
            runtime.select_application(
                str(app_a_dir.resolve()),
                "python",
                str(python_a),
            )
            monkeypatch.setenv("PYTHON_IDENTITY_FILE", str(identity_a))
            await runtime.start_application()
            app_a_pid = runtime.application.process_id

            with pytest.raises(SessionOccupiedError):
                runtime.select_application(
                    str(app_b_dir.resolve()),
                    "python",
                    str(python_b),
                )

            assert Path(identity_a.read_text(encoding="utf-8")) == (
                _expected_application_python(python_a)
            )
            await runtime.stop_application()

            runtime.select_application(
                str(app_b_dir.resolve()),
                "python",
                str(python_b),
            )
            monkeypatch.setenv("PYTHON_IDENTITY_FILE", str(identity_b))
            await runtime.start_application()
            app_b_pid = runtime.application.process_id

            assert Path(identity_b.read_text(encoding="utf-8")) == (
                _expected_application_python(python_b)
            )
            assert app_a_pid is not None
            assert app_b_pid is not None
            assert app_a_pid != app_b_pid
            assert os.getpid() == daemon_pid
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_daemon_selects_only_a_launcher_inside_its_fixed_root(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "application"
    _write_relay_application(app_dir)
    python_executable = Path(sys.executable)
    runtime = DaemonRuntime(
        application_dir=tmp_path / "unselected",
        current_app="unselected",
        managed_app_root=python_executable.parent,
        bundled_resource_root=tmp_path / "bundled-resources",
    )

    selected = runtime.select_application(
        str(app_dir.resolve()),
        "python",
        str(python_executable),
    )

    assert selected["current_app"] == "test_app"
    assert runtime.application.launch_spec is not None
    assert runtime.application.launch_spec.executable == python_executable

    rejected = DaemonRuntime(
        application_dir=tmp_path / "another-unselected",
        current_app="unselected",
        managed_app_root=tmp_path / "managed-only",
        bundled_resource_root=tmp_path / "another-bundled-root",
    )
    with pytest.raises(ApplicationLaunchError) as captured:
        rejected.select_application(
            str(app_dir.resolve()),
            "python",
            str(python_executable),
        )
    assert captured.value.code == "invalid_application_launcher"


def test_preview_frames_prefer_the_active_application_device_channel(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime = DaemonRuntime(
            application_dir=tmp_path / "application",
            current_app="test_app",
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
            preview_udp_port=0,
        )
        runtime.application.registry.begin_start()
        delivered: list[tuple[ApplicationChannel, str | bytes]] = []

        async def capture(
            channel: ApplicationChannel,
            frame: str | bytes,
        ) -> None:
            delivered.append((channel, frame))

        runtime.application.bridge.send_to_application = capture  # type: ignore[method-assign]

        assert await runtime._publish_preview_frame(b"FTW1") == 1
        assert delivered == [(ApplicationChannel.DEVICE, b"FTW1")]

    asyncio.run(scenario())


def test_daemon_starts_without_a_selected_application(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = DaemonRuntime(
            application_dir=tmp_path / "unselected",
            current_app=None,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
            preview_udp_port=0,
        )

        await runtime.start()
        try:
            assert runtime.application_status() == {
                "selected": False,
                "current_app": None,
                "state": "not_selected",
                "process_id": None,
                "last_exit_code": None,
            }
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_application_status_waits_for_supervisor_after_channel_loss(
    tmp_path: Path,
) -> None:
    runtime = DaemonRuntime(
        application_dir=tmp_path / "application",
        current_app="test_app",
        external_host="127.0.0.1",
        external_port=0,
        control_port=0,
        pairing_udp_port=0,
        preview_udp_port=0,
    )
    run = runtime.application.registry.begin_start()
    for channel in ApplicationChannel:
        runtime.application.registry.attach_channel(
            channel,
            credential=run.credential,
        )
    runtime.application.last_state = ApplicationState.RUNNING

    runtime.application.registry.detach_channel(ApplicationChannel.DEVICE)

    assert run.state is ApplicationState.ERROR
    assert runtime.application_status()["state"] == "running"


async def _connect_as(runtime: DaemonRuntime, role: str):
    if role == "hardware":
        return await connect_runtime_hardware(runtime)
    websocket = await connect(runtime.external_server.url, max_size=None)
    await websocket.send(
        json.dumps(
            {
                "type": "sys.client.hello",
                "data": {"role": role},
            }
        )
    )
    await asyncio.wait_for(websocket.recv(), timeout=1)
    return websocket


def test_active_application_owns_routing_then_desktop_control_recovers(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_relay_application(app_dir)
        runtime = DaemonRuntime(
            application_dir=app_dir,
            current_app="test_app",
            managed_app_root=Path(sys.executable).parent,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
        )
        _select_python_application(runtime, app_dir)
        await runtime.start()
        device = await _connect_as(runtime, "hardware")
        desktop = await _connect_as(runtime, "desktop")

        try:
            await runtime.start_application()
            application_pid = runtime.application.process_id

            microphone_open = (
                '{"type":"ctrl.microphone.open","code":0,'
                '"data":{"command_id":"mic-open-001","source":"desktop"}}'
            )
            await desktop.send(microphone_open)
            assert await asyncio.wait_for(desktop.recv(), timeout=1) == microphone_open
            try:
                await asyncio.wait_for(device.recv(), timeout=0.05)
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError(
                    "microphone control bypassed the active Application"
                )

            desktop_text = '{"type":"cmd.desktop","data":{"value":1}}'
            await desktop.send(desktop_text)
            assert await asyncio.wait_for(desktop.recv(), timeout=1) == desktop_text
            try:
                await asyncio.wait_for(device.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError(
                    "desktop frame bypassed the active Application"
                )

            device_binary = b"\x01\x03\x05\x07"
            await device.send(device_binary)
            assert await asyncio.wait_for(device.recv(), timeout=1) == device_binary
            try:
                await asyncio.wait_for(desktop.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError(
                    "device frame bypassed the active Application"
                )

            await device.close()
            replacement_device = await _connect_as(runtime, "hardware")
            assert runtime.application.process_id == application_pid

            replacement_payload = b"\x10\x20"
            await replacement_device.send(replacement_payload)
            assert (
                await asyncio.wait_for(replacement_device.recv(), timeout=1)
                == replacement_payload
            )

            await runtime.stop_application()
            assert runtime.application.process_id is None

            direct_payload = '{"type":"ctrl.direct","data":{}}'
            await desktop.send(direct_payload)
            assert (
                await asyncio.wait_for(replacement_device.recv(), timeout=1)
                == direct_payload
            )
            await replacement_device.close()
        finally:
            await desktop.close()
            await device.close()
            await runtime.stop()

    asyncio.run(scenario())


def test_auto_start_runs_current_app_once_and_does_not_restart_after_exit(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_exiting_application(app_dir)
        runtime = DaemonRuntime(
            application_dir=app_dir,
            current_app="test_app",
            managed_app_root=Path(sys.executable).parent,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
            auto_start_application=True,
        )
        _select_python_application(runtime, app_dir)

        await runtime.start()
        first_pid = runtime.application.process_id
        assert first_pid is not None

        for _ in range(200):
            if runtime.application.last_state.value == "ended":
                break
            await asyncio.sleep(0.01)
        assert runtime.application.process_id is None
        assert runtime.application.last_state.value == "ended"

        await asyncio.sleep(0.2)
        await runtime.start()
        assert runtime.application.process_id is None
        assert runtime.auto_start_attempted is True
        assert runtime.auto_start_error is None

        await runtime.stop()

    asyncio.run(scenario())


def test_daemon_pairing_control_does_not_stop_the_current_application(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_relay_application(app_dir)
        runtime = DaemonRuntime(
            application_dir=app_dir,
            current_app="test_app",
            managed_app_root=Path(sys.executable).parent,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
        )
        _select_python_application(runtime, app_dir)
        await runtime.start()

        try:
            await runtime.start_application()
            application_pid = runtime.application.process_id
            started = await runtime.pair_device("123456", "python_sdk")
            assert started["device"]["state"] == "discovering"
            assert runtime.application.process_id == application_pid

            cancelled = await runtime.cancel_device_pairing()
            assert cancelled["device"]["state"] == "idle"
            assert cancelled["device"]["last_error"] == "pairing_cancelled"
            assert runtime.application.process_id == application_pid
            assert await runtime.disconnect_device() is False
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_device_session_end_releases_daemon_slot_without_stopping_application(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        app_dir = tmp_path / "application"
        _write_relay_application(app_dir)
        runtime = DaemonRuntime(
            application_dir=app_dir,
            current_app="test_app",
            managed_app_root=Path(sys.executable).parent,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
        )
        _select_python_application(runtime, app_dir)
        await runtime.start()
        device = await connect_runtime_hardware(runtime)

        try:
            assert (
                runtime.device_status()["device"][
                    "preview_websocket_url"
                ]
                == "ws://127.0.0.1:81/ws/face-track"
            )
            assert (
                runtime.device_status()["device"]["mjpeg_websocket_url"]
                == "ws://127.0.0.1:82/ws/mjpeg"
            )
            await runtime.start_application()
            application_pid = runtime.application.process_id
            request = runtime.device_pairing.current_request
            assert request is not None

            await device.send(
                json.dumps(
                    {
                        "type": "sys.device.session.end",
                        "code": 0,
                        "data": {
                            "pair_request_id": request.request_id,
                            "reason": "mode_exit",
                        },
                    }
                )
            )
            ack = json.loads(await asyncio.wait_for(device.recv(), timeout=1))
            assert ack["data"]["type"] == "sys.device.session.end"
            await asyncio.wait_for(device.wait_closed(), timeout=1)

            assert runtime.device_status()["device"]["state"] == "idle"
            assert (
                runtime.device_status()["device"][
                    "preview_websocket_url"
                ]
                is None
            )
            assert runtime.device_status()["device"]["mjpeg_websocket_url"] is None
            assert runtime.application.process_id == application_pid
        finally:
            await device.close()
            await runtime.stop()

    asyncio.run(scenario())
