from __future__ import annotations

import asyncio
import json

from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.connections import (
    DeviceConnectionStateRegistry,
    ExternalWebSocketServer,
)


async def _allow_hardware(_hello, _peer_ip: str) -> None:
    return None


async def _connect_hardware(
    server: ExternalWebSocketServer,
):
    websocket = await connect(server.url)
    await websocket.send(
        json.dumps(
            {
                "type": "sys.client.hello",
                "code": 0,
                "data": {
                    "role": "hardware",
                    "pairing_protocol": "watcher-lan-pairing",
                    "pairing_version": "1.0",
                    "pair_request_id": "21a9dbf05ea3443480e62076f79a3b12",
                    "daemon_instance_id": "f730f29e670c49f7a3320c4314eb9805",
                    "session_token": (
                        "f84a1e16ce6f35f14d167f227a93ea93"
                        "d1a9c4d9eb5517112030f2839d57ae4b"
                    ),
                    "mode": "python_sdk",
                },
            }
        )
    )
    await websocket.recv()
    return websocket


def test_device_connection_ids_are_unique_for_registry_lifetime() -> None:
    registry = DeviceConnectionStateRegistry()
    websocket = object()

    first = registry.connect(websocket)
    registry.disconnect(websocket)
    second = registry.connect(websocket)

    assert first.connection_id == 1
    assert second.connection_id == 2


def test_device_connection_state_only_tracks_current_connection() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=_allow_hardware,
        )
        await server.start()
        first = await _connect_hardware(server)
        try:
            [online] = server.registry.device_states.snapshot()
            assert online["online"] is True
            assert set(online) == {"connection_id", "online"}
        finally:
            await first.close()

        for _ in range(100):
            offline = server.registry.device_states.snapshot()
            if not offline:
                break
            await asyncio.sleep(0.01)
        assert offline == []

        second = await _connect_hardware(server)
        try:
            [reconnected] = server.registry.device_states.snapshot()
            assert reconnected["connection_id"] != online["connection_id"]
            assert reconnected["online"] is True
        finally:
            await second.close()
            await server.stop()

    asyncio.run(scenario())
