from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.connections.registry import ExternalClientRole
from watcherobot.runtime.daemon.runtime import DaemonRuntime
from tests.runtime.pairing_helpers import connect_runtime_hardware


VERTICAL_TEST_APPLICATION = """
import asyncio
import os

from websockets.asyncio.client import connect


async def echo(websocket):
    async for frame in websocket:
        await websocket.send(frame)


async def main():
    print("vertical application ready", flush=True)
    async with (
        connect(os.environ["WATCHER_APP_DESKTOP_WS_URL"]) as desktop,
        connect(os.environ["WATCHER_APP_DEVICE_WS_URL"]) as device,
    ):
        await asyncio.gather(echo(desktop), echo(device))


asyncio.run(main())
"""


def _write_test_application(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "vertical_test_app",
                "name": "Vertical Test Application",
                "version": "1.0.0",
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text(
        VERTICAL_TEST_APPLICATION,
        encoding="utf-8",
    )


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
    acknowledgement = json.loads(
        await asyncio.wait_for(websocket.recv(), timeout=1)
    )
    assert acknowledgement["type"] == "sys.ack"
    return websocket


async def _receive_matching(websocket, predicate):
    for _ in range(10):
        frame = await asyncio.wait_for(websocket.recv(), timeout=2)
        if predicate(frame):
            return frame
    raise AssertionError("expected WebSocket frame was not received")


def test_complete_daemon_application_vertical_flow(tmp_path: Path) -> None:
    async def scenario() -> None:
        application_dir = tmp_path / "application"
        log_dir = tmp_path / "logs"
        _write_test_application(application_dir)
        runtime = DaemonRuntime(
            application_dir=application_dir,
            current_app="vertical_test_app",
            python_executable=sys.executable,
            external_host="127.0.0.1",
            external_port=0,
            control_port=0,
            pairing_udp_port=0,
            application_log_dir=log_dir,
        )

        await runtime.start()
        device = await _connect_as(runtime, "hardware")
        desktop = await _connect_as(runtime, "desktop")
        replacement_device = None
        try:
            direct_before_start = '{"type":"direct.before","data":{"step":1}}'
            await desktop.send(direct_before_start)
            assert (
                await asyncio.wait_for(device.recv(), timeout=1)
                == direct_before_start
            )

            async with httpx.AsyncClient() as client:
                started = await client.post(
                    f"{runtime.control_server.base_url}"
                    "/daemon/application/start"
                )
            assert started.status_code == 200
            assert started.json()["application"]["state"] == "running"
            application_pid = runtime.application.process_id
            assert application_pid is not None

            log_frame = await _receive_matching(
                desktop,
                lambda frame: (
                    isinstance(frame, str)
                    and json.loads(frame).get("type")
                    == "daemon.application.log"
                ),
            )
            log_event = json.loads(log_frame)
            assert (
                log_event["data"]["message"]
                == "vertical application ready"
            )

            desktop_application_frame = (
                '{"type":"application.desktop","data":{"step":2}}'
            )
            await desktop.send(desktop_application_frame)
            assert (
                await _receive_matching(
                    desktop,
                    lambda frame: frame == desktop_application_frame,
                )
                == desktop_application_frame
            )
            try:
                await asyncio.wait_for(device.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError(
                    "desktop frame bypassed the active Application"
                )

            device_application_frame = b"\x10\x20\x30\x40"
            await device.send(device_application_frame)
            assert (
                await asyncio.wait_for(device.recv(), timeout=1)
                == device_application_frame
            )

            await device.close()
            replacement_device = await _connect_as(runtime, "hardware")
            assert runtime.application.process_id == application_pid
            await replacement_device.send(b"\x50\x60")
            assert (
                await asyncio.wait_for(replacement_device.recv(), timeout=1)
                == b"\x50\x60"
            )

            async with httpx.AsyncClient() as client:
                stopped = await client.post(
                    f"{runtime.control_server.base_url}"
                    "/daemon/application/stop"
                )
            assert stopped.status_code == 200
            assert stopped.json()["application"]["state"] == "ended"
            assert runtime.application.process_id is None

            direct_after_stop = '{"type":"direct.after","data":{"step":3}}'
            await desktop.send(direct_after_stop)
            assert (
                await asyncio.wait_for(
                    replacement_device.recv(),
                    timeout=1,
                )
                == direct_after_stop
            )

            records = [
                json.loads(line)
                for line in runtime.application_logs.log_path(
                    "vertical_test_app"
                )
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            assert any(
                record["message"] == "vertical application ready"
                and record["stream"] == "stdout"
                for record in records
            )
        finally:
            await desktop.close()
            await device.close()
            if replacement_device is not None:
                await replacement_device.close()
            await runtime.stop()

        assert runtime.application.process_id is None
        assert (
            runtime.connection_registry.online_count(
                ExternalClientRole.DESKTOP
            )
            == 0
        )
        assert (
            runtime.connection_registry.online_count(
                ExternalClientRole.DEVICE
            )
            == 0
        )

    asyncio.run(scenario())
