from __future__ import annotations

import asyncio
import json

from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.connections.websocket_server import (
    ExternalWebSocketServer,
)
from watcherobot.runtime.daemon.preview.face_tracking import (
    FaceTrackingPreviewBroker,
)
from watcherobot.runtime.daemon.application.session import ApplicationChannel


async def _allow_hardware(_hello, _peer_ip: str) -> None:
    return None


def _hello(role: str) -> str:
    data: dict[str, object] = {"role": role}
    if role == "hardware":
        data.update(
            {
                "pairing_protocol": "watcher-lan-pairing",
                "pairing_version": "1.0",
                "pair_request_id": "21a9dbf05ea3443480e62076f79a3b12",
                "daemon_instance_id": "f730f29e670c49f7a3320c4314eb9805",
                "session_token": (
                    "f84a1e16ce6f35f14d167f227a93ea93"
                    "d1a9c4d9eb5517112030f2839d57ae4b"
                ),
                "mode": "desktop_link",
            }
        )
    return json.dumps({"type": "sys.client.hello", "code": 0, "data": data})


async def _connect_as(server: ExternalWebSocketServer, role: str):
    websocket = await connect(server.url, max_size=None)
    await websocket.send(_hello(role))
    acknowledgement = json.loads(
        await asyncio.wait_for(websocket.recv(), timeout=1)
    )
    assert acknowledgement["type"] == "sys.ack"
    return websocket


def test_preview_disconnect_stops_device_without_recentering() -> None:
    async def scenario() -> None:
        broker = FaceTrackingPreviewBroker()
        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=_allow_hardware,
            business_frame_listener=broker.observe_frame,
            external_disconnect_listener=broker.connection_lost,
        )
        broker.bind_registry(server.registry)
        await server.start()
        desktop = await _connect_as(server, "desktop")
        device = await _connect_as(server, "hardware")

        try:
            start = json.dumps(
                {
                    "type": "ctrl.face_tracking.preview.start",
                    "code": 0,
                    "data": {"command_id": "preview-start-1"},
                },
                separators=(",", ":"),
            )
            await desktop.send(start)
            assert await asyncio.wait_for(device.recv(), timeout=1) == start

            await desktop.close()
            stop = json.loads(
                await asyncio.wait_for(device.recv(), timeout=1)
            )
            assert stop["type"] == "ctrl.face_tracking.preview.stop"
            assert stop["data"]["policy"] == "hold"
            assert stop["data"]["command_id"].startswith(
                "daemon-preview-disconnect-"
            )
        finally:
            await desktop.close()
            await device.close()
            await server.stop()

    asyncio.run(scenario())


def test_preview_start_refreshes_udp_listener_before_forwarding() -> None:
    async def scenario() -> None:
        listener_refreshed = asyncio.Event()

        async def refresh_listener() -> None:
            listener_refreshed.set()

        broker = FaceTrackingPreviewBroker(
            on_preview_start=refresh_listener,
        )
        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=_allow_hardware,
            business_frame_listener=broker.observe_frame,
        )
        broker.bind_registry(server.registry)
        await server.start()
        desktop = await _connect_as(server, "desktop")
        device = await _connect_as(server, "hardware")
        try:
            start = json.dumps(
                {
                    "type": "ctrl.face_tracking.preview.start",
                    "data": {"command_id": "preview-start-refresh"},
                }
            )
            await desktop.send(start)
            assert await asyncio.wait_for(device.recv(), timeout=1) == start
            assert listener_refreshed.is_set()
        finally:
            await desktop.close()
            await device.close()
            await server.stop()

    asyncio.run(scenario())


def test_normal_preview_stop_disarms_disconnect_cleanup() -> None:
    async def scenario() -> None:
        broker = FaceTrackingPreviewBroker()
        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=_allow_hardware,
            business_frame_listener=broker.observe_frame,
            external_disconnect_listener=broker.connection_lost,
        )
        broker.bind_registry(server.registry)
        await server.start()
        desktop = await _connect_as(server, "desktop")
        device = await _connect_as(server, "hardware")

        try:
            await desktop.send(
                json.dumps(
                    {
                        "type": "ctrl.face_tracking.preview.start",
                        "data": {"command_id": "preview-start-1"},
                    }
                )
            )
            await asyncio.wait_for(device.recv(), timeout=1)
            await desktop.send(
                json.dumps(
                    {
                        "type": "ctrl.face_tracking.preview.stop",
                        "data": {
                            "command_id": "preview-stop-1",
                            "policy": "hold",
                        },
                    }
                )
            )
            forwarded_stop = json.loads(
                await asyncio.wait_for(device.recv(), timeout=1)
            )
            assert forwarded_stop["data"]["command_id"] == "preview-stop-1"

            await desktop.close()
            try:
                await asyncio.wait_for(device.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError(
                    "normal stop was followed by a duplicate cleanup command"
                )
        finally:
            await desktop.close()
            await device.close()
            await server.stop()

    asyncio.run(scenario())


def test_application_channel_loss_stops_armed_preview() -> None:
    async def scenario() -> None:
        broker = FaceTrackingPreviewBroker()
        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=_allow_hardware,
        )
        broker.bind_registry(server.registry)
        await server.start()
        device = await _connect_as(server, "hardware")
        try:
            await broker.observe_application_frame(
                ApplicationChannel.DEVICE,
                json.dumps(
                    {
                        "type": "ctrl.face_tracking.preview.start",
                        "data": {"command_id": "app-preview-start"},
                    }
                ),
            )
            await broker.application_channel_lost(ApplicationChannel.DEVICE)
            stop = json.loads(await asyncio.wait_for(device.recv(), timeout=1))
            assert stop["type"] == "ctrl.face_tracking.preview.stop"
            assert stop["data"]["policy"] == "hold"
        finally:
            await device.close()
            await server.stop()

    asyncio.run(scenario())
