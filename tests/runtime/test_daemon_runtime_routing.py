from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.runtime import DaemonRuntime
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
            python_executable=sys.executable,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
        )
        await runtime.start()
        device = await _connect_as(runtime, "hardware")
        desktop = await _connect_as(runtime, "desktop")

        try:
            await runtime.start_application()
            application_pid = runtime.application.process_id

            desktop_text = '{"type":"cmd.desktop","data":{"value":1}}'
            await desktop.send(desktop_text)
            assert await asyncio.wait_for(desktop.recv(), timeout=1) == desktop_text
            try:
                await asyncio.wait_for(device.recv(), timeout=0.1)
            except TimeoutError:
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
            except TimeoutError:
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
            python_executable=sys.executable,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
            auto_start_application=True,
        )

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
            python_executable=sys.executable,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
        )
        await runtime.start()

        try:
            await runtime.start_application()
            application_pid = runtime.application.process_id
            started = await runtime.pair_device("123456", "desktop_link")
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
            python_executable=sys.executable,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
        )
        await runtime.start()
        device = await connect_runtime_hardware(runtime)

        try:
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
            assert runtime.application.process_id == application_pid
        finally:
            await device.close()
            await runtime.stop()

    asyncio.run(scenario())
