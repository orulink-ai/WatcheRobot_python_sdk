from __future__ import annotations

import asyncio
import json

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from watcherobot.runtime.daemon.connections import ExternalWebSocketServer
from watcherobot.runtime.daemon.pairing.protocol import PairAccept
from watcherobot.runtime.daemon.pairing.session import DevicePairingSession, DevicePairingState


DAEMON_ID = "f730f29e670c49f7a3320c4314eb9805"
REQUEST_ID = "21a9dbf05ea3443480e62076f79a3b12"
SESSION_TOKEN = (
    "f84a1e16ce6f35f14d167f227a93ea93"
    "d1a9c4d9eb5517112030f2839d57ae4b"
)


def hardware_hello(*, session_token: str = SESSION_TOKEN) -> str:
    return json.dumps(
        {
            "type": "sys.client.hello",
            "code": 0,
            "data": {
                "role": "hardware",
                "pairing_protocol": "watcher-lan-pairing",
                "pairing_version": "1.0",
                "pair_request_id": REQUEST_ID,
                "daemon_instance_id": DAEMON_ID,
                "session_token": session_token,
                "mode": "python_sdk",
            },
        }
    )


def prepared_session() -> DevicePairingSession:
    session = DevicePairingSession(
        daemon_instance_id=DAEMON_ID,
        request_id_factory=lambda: REQUEST_ID,
    )
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=10.0,
    )
    session.accept_device(
        PairAccept(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            target_mode="python_sdk",
            session_token=SESSION_TOKEN,
        ),
        peer_ip="127.0.0.1",
        now=12.0,
    )
    return session


def test_hardware_hello_requires_the_active_pairing_session() -> None:
    async def scenario() -> None:
        session = prepared_session()

        async def authorize(hello, peer_ip: str) -> None:
            session.connect_device(hello, peer_ip=peer_ip, now=14.0)

        async def disconnected(_peer_ip: str) -> None:
            if session.state is DevicePairingState.CONNECTED:
                session.device_disconnected(now=20.0)

        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=authorize,
            device_disconnect_listener=disconnected,
        )
        await server.start()
        websocket = await connect(server.url)
        try:
            await websocket.send(hardware_hello())
            ack = json.loads(await websocket.recv())
            assert ack["type"] == "sys.ack"
            assert ack["data"]["session_state"] == "connected"
            assert ack["data"]["negotiated"]["audio_uplink"]["codec"] == "opus"
            assert session.state is DevicePairingState.CONNECTED
        finally:
            await websocket.close()
            for _ in range(100):
                if session.state is DevicePairingState.RECONNECTING:
                    break
                await asyncio.sleep(0.01)
            await server.stop()

        assert session.state is DevicePairingState.RECONNECTING

    asyncio.run(scenario())

def test_wrong_token_gets_nack_and_credential_close_code() -> None:
    async def scenario() -> None:
        session = prepared_session()

        async def authorize(hello, peer_ip: str) -> None:
            session.connect_device(hello, peer_ip=peer_ip, now=14.0)

        server = ExternalWebSocketServer(
            host="127.0.0.1",
            port=0,
            hardware_hello_authorizer=authorize,
        )
        await server.start()
        websocket = await connect(server.url)
        try:
            await websocket.send(hardware_hello(session_token="0" * 64))
            nack = json.loads(await websocket.recv())
            assert nack["type"] == "sys.nack"
            assert nack["data"]["error"] == "pairing_credential_invalid"
            try:
                await websocket.recv()
            except ConnectionClosedError as exc:
                assert exc.rcvd.code == 4411
            else:
                raise AssertionError("hardware connection should be closed")
            assert session.state is DevicePairingState.CONNECTING
        finally:
            await websocket.close()
            await server.stop()

    asyncio.run(scenario())


def test_legacy_hardware_hello_is_rejected_without_fallback() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(host="127.0.0.1", port=0)
        await server.start()
        websocket = await connect(server.url)
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "sys.client.hello",
                        "data": {
                            "role": "hardware",
                            "device_id": "legacy-device",
                            "mac": "80:B5:4E:EF:AF:8C",
                        },
                    }
                )
            )
            nack = json.loads(await websocket.recv())
            assert nack["type"] == "sys.nack"
            assert nack["data"]["error"] == "pairing_session_required"
            try:
                await websocket.recv()
            except ConnectionClosedError as exc:
                assert exc.rcvd.code == 4410
            else:
                raise AssertionError("legacy hardware connection should be closed")
        finally:
            await websocket.close()
            await server.stop()

    asyncio.run(scenario())


def test_desktop_hello_remains_independent_from_device_pairing() -> None:
    async def scenario() -> None:
        server = ExternalWebSocketServer(host="127.0.0.1", port=0)
        await server.start()
        websocket = await connect(server.url)
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "sys.client.hello",
                        "data": {"role": "desktop"},
                    }
                )
            )
            ack = json.loads(await websocket.recv())
            assert ack["type"] == "sys.ack"
            assert ack["data"] == {
                "type": "sys.client.hello",
                "role": "desktop",
            }
        finally:
            await websocket.close()
            await server.stop()

    asyncio.run(scenario())
