from __future__ import annotations

import asyncio
import base64
import json
import os

import pytest
from websockets.asyncio.client import connect

from watcherobot.protocol import FLAG_FIRST, FLAG_LAST, FRAME_RECORDING, build_wspk
from watcherobot.runtime.daemon.connections import ExternalClientRole, ExternalWebSocketServer


async def _read_server_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    header = await reader.readexactly(2)
    length = header[1] & 0x7F
    if length == 126:
        length = int.from_bytes(await reader.readexactly(2), "big")
    elif length == 127:
        length = int.from_bytes(await reader.readexactly(8), "big")
    assert not header[1] & 0x80, "server frames must not be masked"
    return header[0] & 0x0F, await reader.readexactly(length)


def test_silent_power_loss_removes_device_but_healthy_idle_device_stays_online() -> None:
    async def scenario() -> None:
        from websockets.frames import Frame, OP_TEXT

        disconnected = asyncio.Event()
        server = ExternalWebSocketServer(
            host="127.0.0.1", port=0, hardware_hello_authorizer=_allow_hardware,
            device_disconnect_listener=lambda _peer: disconnected.set(),
        )
        await server.start()
        writer = None
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
            key = base64.b64encode(os.urandom(16)).decode()
            writer.write((f"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                          "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            await writer.drain()
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=1)
            assert b"101 Switching Protocols" in headers
            writer.write(Frame(OP_TEXT, _hello("hardware").encode()).serialize(mask=True))
            await writer.drain()
            # A real WebSocket ACK proves this is an authorized device, then the
            # raw peer deliberately never sends pong or a TCP FIN (power loss).
            opcode, ack = await asyncio.wait_for(_read_server_frame(reader), timeout=1)
            assert opcode == OP_TEXT
            assert json.loads(ack)["type"] == "sys.ack"
            assert server.registry.online_count(ExternalClientRole.DEVICE) == 1
            # LAN liveness must be cleared within six seconds even when TCP
            # remains open: 1 s to ping, 3 s for pong, 1 s to close + margin.
            await asyncio.wait_for(disconnected.wait(), timeout=6)
            assert server.registry.online_count(ExternalClientRole.DEVICE) == 0

            disconnected.clear()
            healthy = await _connect_as(server, "hardware")
            desktop = await _connect_as(server, "desktop")
            try:
                await asyncio.sleep(11)
                assert server.registry.online_count(ExternalClientRole.DEVICE) == 1
                assert server.registry.online_count(ExternalClientRole.DESKTOP) == 1
                assert not disconnected.is_set()
                for _ in range(20):
                    payload = b"\x00\x01" * 1024
                    await desktop.send(payload)
                    assert await asyncio.wait_for(healthy.recv(), timeout=1) == payload
                await desktop.close()
                replacement = await _connect_as(server, "desktop")
                await replacement.close()
            finally:
                await desktop.close()
                await healthy.close()
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await server.stop()

    asyncio.run(scenario())


def test_orderly_device_close_does_not_wait_for_heartbeat_deadline() -> None:
    async def scenario() -> None:
        disconnected = asyncio.Event()
        server = ExternalWebSocketServer(
            host="127.0.0.1", port=0, hardware_hello_authorizer=_allow_hardware,
            device_disconnect_listener=lambda _peer: disconnected.set(),
        )
        await server.start()
        try:
            device = await _connect_as(server, "hardware")
            await asyncio.wait_for(
                asyncio.gather(device.close(), disconnected.wait()), timeout=2.5
            )
            assert server.registry.online_count(ExternalClientRole.DEVICE) == 0
        finally:
            await server.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("orderly", [True, False], ids=["close", "missed-pong"])
def test_device_disconnect_does_not_wait_for_business_forwarding(orderly: bool) -> None:
    async def scenario() -> None:
        from websockets.frames import Close, Frame, OP_BINARY, OP_CLOSE, OP_TEXT

        entered = asyncio.Event()
        release_forwarding = asyncio.Event()
        cancelled = asyncio.Event()
        disconnected = asyncio.Event()
        disconnects: list[str] = []

        async def hold_forwarding(_connection, _frame):
            entered.set()
            try:
                await release_forwarding.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release_forwarding.wait()
                raise

        def record_disconnect(peer: str) -> None:
            disconnects.append(peer)
            disconnected.set()

        server = ExternalWebSocketServer(
            host="127.0.0.1", port=0, hardware_hello_authorizer=_allow_hardware,
            business_frame_listener=hold_forwarding,
            device_disconnect_listener=record_disconnect,
        )
        await server.start()
        writer = None
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
            key = base64.b64encode(os.urandom(16)).decode()
            writer.write((f"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                          "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            assert b"101 Switching Protocols" in await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 1)
            writer.write(Frame(OP_TEXT, _hello("hardware").encode()).serialize(mask=True))
            _, ack = await asyncio.wait_for(_read_server_frame(reader), 1)
            assert json.loads(ack)["type"] == "sys.ack"
            writer.write(Frame(OP_BINARY, b"pending business frame").serialize(mask=True))
            await asyncio.wait_for(entered.wait(), 1)
            assert server.registry.online_count(ExternalClientRole.DEVICE) == 1

            if orderly:
                writer.write(Frame(OP_CLOSE, Close(1000, "").serialize()).serialize(mask=True))
            await asyncio.wait_for(disconnected.wait(), 2 if orderly else 6)
            await asyncio.wait_for(cancelled.wait(), 1)
            assert server.registry.online_count(ExternalClientRole.DEVICE) == 0

            replacement = await _connect_as(server, "hardware")
            try:
                release_forwarding.set()
                await asyncio.sleep(0)
                assert disconnects == ["127.0.0.1"]
                assert server.registry.online_count(ExternalClientRole.DEVICE) == 1
            finally:
                await replacement.close()
        finally:
            release_forwarding.set()
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await server.stop()

    asyncio.run(scenario())


def test_lan_ping_starts_within_one_second_and_tolerates_two_second_pong_delay() -> None:
    async def scenario() -> None:
        from websockets.frames import Frame, OP_PING, OP_PONG, OP_TEXT

        disconnected = asyncio.Event()
        server = ExternalWebSocketServer(
            host="127.0.0.1", port=0, hardware_hello_authorizer=_allow_hardware,
            device_disconnect_listener=lambda _peer: disconnected.set(),
        )
        await server.start()
        writer = None
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
            key = base64.b64encode(os.urandom(16)).decode()
            writer.write((f"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                          "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            await writer.drain()
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=1)
            assert b"101 Switching Protocols" in headers
            writer.write(Frame(OP_TEXT, _hello("hardware").encode()).serialize(mask=True))
            await writer.drain()
            _, ack = await asyncio.wait_for(_read_server_frame(reader), timeout=1)
            assert json.loads(ack)["type"] == "sys.ack"
            opcode, ping = await asyncio.wait_for(_read_server_frame(reader), timeout=1.5)
            assert opcode == OP_PING
            await asyncio.sleep(2)
            assert not disconnected.is_set()
            writer.write(Frame(OP_PONG, ping).serialize(mask=True))
            await writer.drain()
            # Survive the original unanswered-ping deadline after the late pong.
            await asyncio.sleep(1.5)
            assert server.registry.online_count(ExternalClientRole.DEVICE) == 1
            assert not disconnected.is_set()
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await server.stop()

    asyncio.run(scenario())


async def _allow_hardware(_hello, _peer_ip: str) -> None:
    return None


def test_handshake_events_report_stage_without_credentials() -> None:
    async def scenario() -> None:
        from watcherobot.runtime.daemon.pairing.session import PairingSessionError
        events = []
        def reject(_hello, _peer):
            raise PairingSessionError("pairing_credential_invalid")
        server = ExternalWebSocketServer(host="127.0.0.1", port=0,
                                         hardware_hello_authorizer=reject, event_logger=events.append)
        await server.start()
        try:
            async with connect(server.url) as device:
                await device.send(_hello("hardware"))
                nack = json.loads(await asyncio.wait_for(device.recv(), timeout=1))
                assert nack["data"]["error"] == "pairing_credential_invalid"
            assert any("WebSocket connection opened" in event for event in events)
            assert any("Hardware hello rejected" in event and "pairing_credential_invalid" in event for event in events)
            assert all("session_token" not in event and "f84a1e16" not in event for event in events)
        finally:
            await server.stop()
    asyncio.run(scenario())


def test_connection_logger_failure_does_not_break_handshake() -> None:
    async def scenario() -> None:
        def fail(_message):
            raise OSError("log storage unavailable")
        server = ExternalWebSocketServer(host="127.0.0.1", port=0,
                                         hardware_hello_authorizer=_allow_hardware, event_logger=fail)
        await server.start()
        try:
            device = await _connect_as(server, "hardware")
            await device.close()
        finally:
            await server.stop()
    asyncio.run(scenario())


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
                    "mode": "python_sdk",
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
            device_binary = build_wspk(
                FRAME_RECORDING, FLAG_FIRST | FLAG_LAST, 42, 0, b"recording-chunk"
            )
            await device.send(device_text)
            await device.send(device_binary)
            assert await asyncio.wait_for(desktop.recv(), timeout=1) == device_text
            assert await asyncio.wait_for(desktop.recv(), timeout=1) == device_binary
        finally:
            await desktop.close()
            await device.close()
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
