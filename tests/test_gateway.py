import asyncio
import inspect
import json
import threading

import pytest
import websockets

from watcherobot import JobCancelledError, JobFailedError, WatcheRobot
from watcherobot.errors import ConnectionTimeoutError
from watcherobot.protocol import FLAG_FIRST, FLAG_LAST, FRAME_AUDIO, ProtocolError, parse_wspk
from watcherobot.transport import BackgroundTransport, _parse_capabilities
from tests.fakes import FakeRobot


def _loopback_connect(uri: str):
    options = {}
    if "proxy" in inspect.signature(websockets.connect).parameters:
        options["proxy"] = None
    return websockets.connect(uri, **options)


def test_gateway_pairs_and_correlates_command_ack():
    asyncio.run(_gateway_round_trip())


def test_gateway_reports_ready_event_timeout():
    asyncio.run(_gateway_ready_event_timeout())


def test_gateway_rejects_websocket_hello_with_wrong_pairing_code():
    asyncio.run(_gateway_rejects_wrong_hello_pairing_code())


def test_gateway_loopback_ignores_system_proxy(monkeypatch):
    monkeypatch.setenv("ws_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    asyncio.run(_gateway_round_trip())


def test_fake_robot_drives_job_completion_failure_cancel_and_disconnect():
    asyncio.run(_gateway_job_lifecycle())


def test_audio_stream_uses_ordered_wspk_chunks():
    asyncio.run(_audio_stream_chunks())


def test_audio_stream_waits_for_device_buffer_credit():
    asyncio.run(_audio_stream_backpressure())


def test_command_timeout_preserves_explicit_zero():
    transport = BackgroundTransport(command_timeout=5.0)

    assert transport._effective_timeout(None) == 5.0
    assert transport._effective_timeout(0) == 0


def test_ready_capabilities_require_a_list_of_non_empty_strings():
    assert _parse_capabilities(["behavior", "camera.capture"]) == (
        "behavior",
        "camera.capture",
    )
    for invalid in ("behavior", ["behavior", 7], ["behavior", ""]):
        with pytest.raises(ProtocolError, match="capabilities"):
            _parse_capabilities(invalid)


async def _audio_stream_chunks():
    class FakeWebSocket:
        def __init__(self):
            self.frames = []

        async def send(self, frame):
            self.frames.append(frame)

    transport = BackgroundTransport()
    websocket = FakeWebSocket()
    transport._websocket = websocket

    await transport._send_audio_stream(b"1234567890", stream_id=7, chunk_bytes=4)

    frames = [parse_wspk(raw) for raw in websocket.frames]
    assert [frame.frame_type for frame in frames] == [FRAME_AUDIO] * 3
    assert [frame.stream_id for frame in frames] == [7, 7, 7]
    assert [frame.sequence for frame in frames] == [0, 1, 2]
    assert [frame.flags for frame in frames] == [FLAG_FIRST, 0, FLAG_LAST]
    assert [frame.payload for frame in frames] == [b"1234", b"5678", b"90"]


async def _audio_stream_backpressure():
    class FakeWebSocket:
        def __init__(self):
            self.frames = []

        async def send(self, frame):
            self.frames.append(frame)

    transport = BackgroundTransport(command_timeout=1.0)
    websocket = FakeWebSocket()
    transport._websocket = websocket
    sender = asyncio.create_task(
        transport._send_audio_stream(b"123456789012", stream_id=7, chunk_bytes=2)
    )

    await asyncio.sleep(0.01)
    assert len(websocket.frames) == 4
    assert not sender.done()

    await transport._dispatch_message(
        {
            "type": "evt.audio.buffer_status",
            "code": 0,
            "data": {
                "stream_id": 7,
                "reason": "playback",
                "pending_frames": 6,
                "free_frames": 58,
                "queue_depth": 64,
            },
        }
    )
    await sender
    assert len(websocket.frames) == 6


async def _gateway_ready_event_timeout():
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

    async with _loopback_connect(f"ws://127.0.0.1:{transport.websocket_port}") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "sys.client.hello",
                    "code": 0,
                    "data": {
                        "device_id": "watcher-test",
                        "fw_version": "V1.0",
                        "pairing_code": "123456",
                    },
                }
            )
        )
        hello_ack = json.loads(await websocket.recv())
        assert hello_ack["data"]["type"] == "sys.client.hello"
        await asyncio.to_thread(starter.join, 1)

    assert not starter.is_alive()
    assert len(start_error) == 1
    assert isinstance(start_error[0], ConnectionTimeoutError)
    assert "SDK ready event" in str(start_error[0])
    await asyncio.to_thread(transport.close)


async def _gateway_rejects_wrong_hello_pairing_code():
    transport = BackgroundTransport(discovery_port=0, websocket_port=0, command_timeout=0.05)
    start_error = []

    def start_transport():
        try:
            transport.start("123456", timeout=0.2)
        except Exception as error:  # pragma: no cover - asserted below
            start_error.append(error)

    starter = threading.Thread(target=start_transport)
    starter.start()
    assert await asyncio.to_thread(transport._started_event.wait, 1)

    async with _loopback_connect(f"ws://127.0.0.1:{transport.websocket_port}") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "sys.client.hello",
                    "code": 0,
                    "data": {
                        "device_id": "watcher-test",
                        "fw_version": "V1.0",
                        "pairing_code": "654321",
                    },
                }
            )
        )
        rejection = json.loads(await websocket.recv())
        assert rejection == {
            "type": "sys.nack",
            "code": 1,
            "data": {"type": "sys.client.hello", "reason": "authentication_failed"},
        }

    await asyncio.to_thread(starter.join, 1)
    assert not starter.is_alive()
    assert len(start_error) == 1
    assert isinstance(start_error[0], ConnectionTimeoutError)
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

    async with _loopback_connect(f"ws://127.0.0.1:{transport.websocket_port}") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "sys.client.hello",
                    "code": 0,
                    "data": {
                        "device_id": "watcher-test",
                        "fw_version": "V1.0",
                        "mac": "00:11:22:33:44:55",
                        "pairing_code": "123456",
                    },
                }
            )
        )
        hello_ack = json.loads(await websocket.recv())
        assert hello_ack["data"]["type"] == "sys.client.hello"
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


async def _gateway_job_lifecycle():
    transport = BackgroundTransport(discovery_port=0, websocket_port=0)
    sdk_robot = WatcheRobot._from_transport(transport)
    start_error = []

    def start_transport():
        try:
            transport.start("123456", timeout=3)
        except Exception as error:  # pragma: no cover - asserted below
            start_error.append(error)

    starter = threading.Thread(target=start_transport)
    starter.start()
    assert await asyncio.to_thread(transport._started_event.wait, 2)

    async with await FakeRobot.connect(transport.websocket_port) as robot:
        authenticate = await robot.begin_pairing()
        await robot.accept_pairing(authenticate)
        await asyncio.to_thread(starter.join, 2)
        assert not starter.is_alive()
        assert start_error == []

        completed_future = asyncio.create_task(
            asyncio.to_thread(sdk_robot.behavior.play, "greeting")
        )
        completed_command = await robot.receive()
        await robot.ack(completed_command, operation_id=21)
        completed = await completed_future
        await robot.operation(21, "running")
        await robot.operation(21, "completed")
        assert await asyncio.to_thread(completed.wait, 1) is completed

        failed_future = asyncio.create_task(
            asyncio.to_thread(sdk_robot.behavior.play, "blocked")
        )
        failed_command = await robot.receive()
        await robot.ack(failed_command, operation_id=22)
        failed = await failed_future
        await robot.operation(22, "failed", error_code=17, reason="servo_stalled")
        with pytest.raises(JobFailedError, match="servo_stalled"):
            await asyncio.to_thread(failed.wait, 1)

        cancelled_future = asyncio.create_task(
            asyncio.to_thread(sdk_robot.behavior.play, "cancel-me")
        )
        cancelled_command = await robot.receive()
        await robot.ack(cancelled_command, operation_id=23)
        cancelled = await cancelled_future
        cancel_future = asyncio.create_task(asyncio.to_thread(cancelled.cancel))
        cancel_command = await robot.receive()
        assert cancel_command["type"] == "ctrl.job.cancel"
        await robot.ack(cancel_command)
        await cancel_future
        await robot.operation(23, "cancelled")
        with pytest.raises(JobCancelledError):
            await asyncio.to_thread(cancelled.wait, 1)

        disconnected_future = asyncio.create_task(
            asyncio.to_thread(sdk_robot.behavior.play, "disconnect-me")
        )
        disconnected_command = await robot.receive()
        await robot.ack(disconnected_command, operation_id=24)
        disconnected = await disconnected_future

    with pytest.raises(JobFailedError, match="disconnected"):
        await asyncio.to_thread(disconnected.wait, 1)
    await asyncio.to_thread(sdk_robot.close)
