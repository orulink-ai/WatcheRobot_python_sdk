from __future__ import annotations

import asyncio
import json

from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.connections import ExternalClientRole, ExternalWebSocketServer


async def _allow_hardware(_hello, _peer_ip: str) -> None:
    return None


def _hello(role: str, **metadata) -> str:
    if role == "hardware":
        return json.dumps(
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
                    "mode": "desktop_link",
                    **metadata,
                },
            }
        )
    return json.dumps(
        {
            "type": "sys.client.hello",
            "data": {"role": role, **metadata},
        }
    )


async def _connect_as(server: ExternalWebSocketServer, role: str):
    websocket = await connect(server.url, max_size=None)
    await websocket.send(_hello(role))
    ack = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
    assert ack["type"] == "sys.ack"
    assert ack["data"]["type"] == "sys.client.hello"
    assert ack["data"]["role"] == role
    return websocket


def test_no_application_routes_desktop_and_device_frames_unchanged() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=_allow_hardware,
        )
        await server.start()
        desktop = await _connect_as(server, "desktop")
        device = await _connect_as(server, "hardware")

        try:
            desktop_text = '{"type":"ctrl.test","data":{"value":1}}'
            desktop_binary = b"\x00\x01\xfe\xff"
            await desktop.send(desktop_text)
            await desktop.send(desktop_binary)
            assert await asyncio.wait_for(device.recv(), timeout=1) == desktop_text
            assert await asyncio.wait_for(device.recv(), timeout=1) == desktop_binary

            device_text = '{"type":"evt.test","data":{"online":true}}'
            device_binary = b"\x10\x20\x30"
            await device.send(device_text)
            await device.send(device_binary)
            assert await asyncio.wait_for(desktop.recv(), timeout=1) == device_text
            assert await asyncio.wait_for(desktop.recv(), timeout=1) == device_binary
        finally:
            await desktop.close()
            await device.close()
            await server.stop()

    asyncio.run(scenario())


def test_desktop_hello_preserves_preview_delivery_capability() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(host="127.0.0.1", port=0)
        await server.start()
        desktop = await connect(server.url, max_size=None)
        await desktop.send(
            _hello(
                "desktop",
                client_name="media-debugger",
                capabilities=["face_tracking.preview.credit.v1"],
            )
        )
        await asyncio.wait_for(desktop.recv(), timeout=1)
        try:
            [connection] = server.registry.connections_for(
                ExternalClientRole.DESKTOP
            )
            assert connection.metadata == {
                "client_name": "media-debugger",
                "capabilities": ["face_tracking.preview.credit.v1"],
            }
        finally:
            await desktop.close()
            await server.stop()

    asyncio.run(scenario())


def test_desktop_role_is_restricted_to_loopback_peers() -> None:
    assert ExternalWebSocketServer.is_loopback_address("127.0.0.1")
    assert ExternalWebSocketServer.is_loopback_address("::1")
    assert not ExternalWebSocketServer.is_loopback_address("192.0.2.10")
    assert not ExternalWebSocketServer.is_loopback_address("")


def test_external_connection_requires_one_valid_role_declaration() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(host="127.0.0.1", port=0)
        await server.start()
        try:
            unregistered = await connect(server.url)
            await unregistered.send(b"\x01\x02")
            await asyncio.wait_for(unregistered.wait_closed(), timeout=1)
            assert unregistered.close_code == server.CLOSE_ROLE_REQUIRED

            invalid = await connect(server.url)
            await invalid.send(_hello("application"))
            await asyncio.wait_for(invalid.wait_closed(), timeout=1)
            assert invalid.close_code == server.CLOSE_INVALID_ROLE

            duplicate = await _connect_as(server, "desktop")
            await duplicate.send(_hello("desktop"))
            await asyncio.wait_for(duplicate.wait_closed(), timeout=1)
            assert duplicate.close_code == server.CLOSE_ROLE_LOCKED
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_external_connection_requires_hello_within_configured_deadline() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hello_timeout_seconds=0.05,
        )
        await server.start()
        client = await connect(server.url)
        try:
            nack = json.loads(await asyncio.wait_for(client.recv(), timeout=1))
            assert nack == {
                "type": "sys.nack",
                "code": 401,
                "data": {
                    "type": "sys.client.hello",
                    "error": "client_hello_required",
                },
            }
            await asyncio.wait_for(client.wait_closed(), timeout=1)
            assert client.close_code == server.CLOSE_ROLE_REQUIRED
        finally:
            await client.close()
            await server.stop()

    asyncio.run(scenario())


def test_hardware_hello_rejects_legacy_metadata_without_fallback() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(host="127.0.0.1", port=0)
        await server.start()
        device = await connect(server.url)
        await device.send(
            _hello(
                "hardware",
                device_id="watcher-test",
                mac="02:11:22:33:44:55",
                firmware_version="1.2.3",
                protocol_version="0.1.6",
                capabilities={"audio_uplink": ["pcm", "opus"]},
            )
        )
        nack = json.loads(await asyncio.wait_for(device.recv(), timeout=1))

        try:
            assert nack == {
                "type": "sys.nack",
                "code": 401,
                "data": {
                    "type": "sys.client.hello",
                    "error": "pairing_session_required",
                },
            }
            await device.wait_closed()
            assert device.close_code == server.CLOSE_PAIRING_SESSION_REQUIRED
            assert server.registry.online_count(ExternalClientRole.DEVICE) == 0
        finally:
            await device.close()

        for _ in range(100):
            if server.registry.online_count(ExternalClientRole.DEVICE) == 0:
                break
            await asyncio.sleep(0.01)
        assert server.registry.online_count(ExternalClientRole.DEVICE) == 0
        await server.stop()

    asyncio.run(scenario())


def test_daemon_handles_connection_ping_without_routing_it() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=_allow_hardware,
        )
        await server.start()
        desktop = await _connect_as(server, "desktop")
        device = await _connect_as(server, "hardware")

        try:
            await desktop.send(
                json.dumps({"type": "sys.ping", "data": {"nonce": "test"}})
            )
            pong = json.loads(await asyncio.wait_for(desktop.recv(), timeout=1))
            assert pong == {"type": "sys.pong", "code": 0, "data": {}}
            try:
                await asyncio.wait_for(device.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError("connection ping was routed as a business frame")
        finally:
            await desktop.close()
            await device.close()
            await server.stop()

    asyncio.run(scenario())


def test_device_session_end_is_acknowledged_and_not_routed() -> None:
    async def scenario() -> None:
        ended = []

        async def handle_session_end(message, peer_ip: str) -> None:
            ended.append((message.pair_request_id, message.reason, peer_ip))

        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=_allow_hardware,
            device_session_end_listener=handle_session_end,
        )
        await server.start()
        desktop = await _connect_as(server, "desktop")
        device = await _connect_as(server, "hardware")

        try:
            await device.send(
                json.dumps(
                    {
                        "type": "sys.device.session.end",
                        "code": 0,
                        "data": {
                            "pair_request_id": "21a9dbf05ea3443480e62076f79a3b12",
                            "reason": "mode_exit",
                        },
                    }
                )
            )
            ack = json.loads(await asyncio.wait_for(device.recv(), timeout=1))
            assert ack == {
                "type": "sys.ack",
                "code": 0,
                "data": {"type": "sys.device.session.end"},
            }
            await asyncio.wait_for(device.wait_closed(), timeout=1)
            assert device.close_code == 1000
            assert ended and ended[0][:2] == (
                "21a9dbf05ea3443480e62076f79a3b12",
                "mode_exit",
            )
            try:
                await asyncio.wait_for(desktop.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            else:
                raise AssertionError("device session end was routed as business data")
        finally:
            await desktop.close()
            await device.close()
            await server.stop()

    asyncio.run(scenario())
