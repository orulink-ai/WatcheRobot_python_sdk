import asyncio
import json
import threading

import websockets

from watcherobot.errors import ConnectionTimeoutError
from watcherobot.transport import BackgroundTransport


def test_gateway_pairs_and_correlates_command_ack():
    asyncio.run(_gateway_round_trip())


def test_gateway_reports_authentication_response_timeout():
    asyncio.run(_gateway_authentication_timeout())


async def _gateway_authentication_timeout():
    transport = BackgroundTransport(discovery_port=0, websocket_port=0, command_timeout=0.05)
    start_error = []

    def start_transport():
        try:
            transport.start("123456", timeout=1)
        except Exception as error:  # pragma: no cover - asserted below
            start_error.append(error)

    starter = threading.Thread(target=start_transport)
    starter.start()
    assert await asyncio.to_thread(transport._started_event.wait, 1)

    async with websockets.connect(f"ws://127.0.0.1:{transport.websocket_port}") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "sys.client.hello",
                    "code": 0,
                    "data": {"device_id": "watcher-test", "fw_version": "V1.0"},
                }
            )
        )
        await websocket.recv()
        authenticate = json.loads(await websocket.recv())
        assert authenticate["type"] == "sys.sdk.authenticate"
        await asyncio.to_thread(starter.join, 1)

    assert not starter.is_alive()
    assert len(start_error) == 1
    assert isinstance(start_error[0], ConnectionTimeoutError)
    assert "SDK authentication response" in str(start_error[0])
    await asyncio.to_thread(transport.close)


async def _gateway_round_trip():
    received_events = []
    disconnected = threading.Event()
    transport = BackgroundTransport(discovery_port=0, websocket_port=0)
    transport.set_callbacks(received_events.append, lambda frame: None, disconnected.set)
    start_error = []

    def start_transport():
        try:
            transport.start("123456", timeout=3)
        except Exception as error:  # pragma: no cover - asserted below
            start_error.append(error)

    starter = threading.Thread(target=start_transport)
    starter.start()
    assert await asyncio.to_thread(transport._started_event.wait, 2)

    async with websockets.connect(f"ws://127.0.0.1:{transport.websocket_port}") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "sys.client.hello",
                    "code": 0,
                    "data": {"device_id": "watcher-test", "fw_version": "V1.0", "mac": "00:11:22:33:44:55"},
                }
            )
        )
        hello_ack = json.loads(await websocket.recv())
        assert hello_ack["data"]["type"] == "sys.client.hello"

        authenticate = json.loads(await websocket.recv())
        assert authenticate["type"] == "sys.sdk.authenticate"
        assert authenticate["data"]["pairing_code"] == "123456"
        command_id = authenticate["data"]["command_id"]
        await websocket.send(
            json.dumps(
                {
                    "type": "sys.ack",
                    "code": 0,
                    "data": {"type": "sys.sdk.authenticate", "command_id": command_id},
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "evt.sdk.ready",
                    "code": 0,
                    "data": {
                        "protocol_version": "1.0",
                        "device_id": "watcher-test",
                        "firmware_version": "V1.0",
                        "capabilities": ["behavior", "motion"],
                    },
                }
            )
        )

        await asyncio.to_thread(starter.join, 2)
        assert not starter.is_alive()
        assert start_error == []
        assert transport.capabilities == ("behavior", "motion")

        command_future = asyncio.create_task(
            asyncio.to_thread(transport.send_command, "ctrl.behavior.play", {"behavior_id": "greeting"})
        )
        command = json.loads(await websocket.recv())
        await websocket.send(
            json.dumps(
                {
                    "type": "sys.ack",
                    "code": 0,
                    "data": {
                        "type": command["type"],
                        "command_id": command["data"]["command_id"],
                        "operation_id": 7,
                    },
                }
            )
        )
        response = await command_future
        assert response["data"]["operation_id"] == 7

        operation_event = {
            "type": "evt.sdk.operation",
            "code": 0,
            "data": {"operation_id": 7, "domain": "behavior", "state": "running"},
        }
        await websocket.send(json.dumps(operation_event))
        for _ in range(20):
            if received_events:
                break
            await asyncio.sleep(0.01)
        assert received_events == [operation_event]

    assert await asyncio.to_thread(disconnected.wait, 1)
    await asyncio.to_thread(transport.close)
