from __future__ import annotations

import asyncio
import json

from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.pairing.protocol import PairAccept, encode_udp_message
from watcherobot.runtime.daemon.pairing.session import DevicePairingState


SESSION_TOKEN = (
    "f84a1e16ce6f35f14d167f227a93ea93"
    "d1a9c4d9eb5517112030f2839d57ae4b"
)


def hardware_hello(runtime) -> str:
    request = runtime.device_pairing.current_request
    assert request is not None
    return json.dumps(
        {
            "type": "sys.client.hello",
            "code": 0,
            "data": {
                "role": "hardware",
                "pairing_protocol": "watcher-lan-pairing",
                "pairing_version": "1.0",
                "pair_request_id": request.request_id,
                "daemon_instance_id": runtime.device_pairing.daemon_instance_id,
                "session_token": SESSION_TOKEN,
                "mode": "desktop_link",
            },
        }
    )


async def prepare_runtime_pairing(runtime) -> None:
    if runtime.device_pairing.state is DevicePairingState.CONNECTED:
        for _ in range(100):
            if runtime.device_pairing.state is DevicePairingState.RECONNECTING:
                break
            await asyncio.sleep(0.01)
    if runtime.device_pairing.state is DevicePairingState.IDLE:
        await runtime.pair_device("123456", "desktop_link")
        request = runtime.device_pairing.current_request
        assert request is not None
        accepted = PairAccept(
            request_id=request.request_id,
            daemon_instance_id=runtime.device_pairing.daemon_instance_id,
            target_mode="desktop_link",
            session_token=SESSION_TOKEN,
        )
        assert await runtime.pairing_udp.handle_datagram(
            encode_udp_message(accepted),
            ("127.0.0.1", 37021),
        )
    assert runtime.device_pairing.state in {
        DevicePairingState.CONNECTING,
        DevicePairingState.RECONNECTING,
    }


async def connect_runtime_hardware(runtime):
    await prepare_runtime_pairing(runtime)
    websocket = await connect(runtime.external_server.url, max_size=None)
    await websocket.send(hardware_hello(runtime))
    acknowledgement = json.loads(await websocket.recv())
    assert acknowledgement["type"] == "sys.ack"
    assert acknowledgement["data"]["session_state"] == "connected"
    return websocket
