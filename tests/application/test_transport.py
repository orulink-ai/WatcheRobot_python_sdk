from __future__ import annotations

import asyncio
import json
import struct

from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.protocol import FRAME_VIDEO
from watcherobot.runtime.daemon.application.session import ApplicationChannel


def test_connected_application_requests_current_device_capabilities() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[tuple[ApplicationChannel, str | bytes]] = []
        command_sent = asyncio.Event()

        async def capture(
            channel: ApplicationChannel,
            frame: str | bytes,
        ) -> None:
            sent.append((channel, frame))
            command_sent.set()

        transport._send = capture  # type: ignore[method-assign]

        connected = asyncio.create_task(transport._on_channels_connected())
        await asyncio.wait_for(command_sent.wait(), timeout=0.1)

        assert not connected.done()
        assert not transport._started_event.is_set()

        assert len(sent) == 1
        channel, frame = sent[0]
        assert channel is ApplicationChannel.DEVICE
        assert isinstance(frame, str)
        payload = json.loads(frame)
        assert payload["type"] == "sys.sdk.ready.get"
        assert payload["code"] == 0
        assert isinstance(payload["data"]["command_id"], str)
        assert payload["data"]["command_id"]
        command_id = payload["data"]["command_id"]
        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.sdk.ready",
                    "code": 0,
                    "data": {
                        "capabilities": ["behavior", "motion"],
                        "firmware_version": "V3.1",
                    },
                }
            ),
        )
        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "sys.ack",
                    "code": 0,
                    "data": {"command_id": command_id},
                }
            ),
        )
        await asyncio.wait_for(connected, timeout=0.1)

        assert transport.capabilities == ("behavior", "motion")
        assert transport.device_info["firmware_version"] == "V3.1"
        assert transport._started_event.is_set()

    asyncio.run(scenario())


def test_audio_stream_waits_for_device_buffer_credit() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(
            channel: ApplicationChannel,
            frame: str | bytes,
        ) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        task = asyncio.create_task(
            transport._send_audio_stream(
                b"\x00\x01" * 5,
                stream_id=7,
                chunk_bytes=2,
            )
        )

        for _ in range(100):
            if len(sent) == 4:
                break
            await asyncio.sleep(0)
        assert len(sent) == 4
        assert not task.done()

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.audio.buffer_status",
                    "code": 0,
                    "data": {
                        "stream_id": 7,
                        "pending_frames": 3,
                        "queue_depth": 8,
                    },
                }
            ),
        )
        await asyncio.wait_for(task, timeout=1.0)
        assert len(sent) == 5

    asyncio.run(scenario())


def test_transport_dispatches_face_preview_packet_as_video_frame() -> None:
    transport = DaemonApplicationTransport()
    received = []
    transport.set_callbacks(lambda _message: None, received.append, lambda: None)
    jpeg = b"\xff\xd8preview\xff\xd9"
    packet = struct.pack(
        "<4sBBHIIHHI",
        b"FTW1",
        1,
        1,
        24,
        42,
        1000,
        416,
        416,
        len(jpeg),
    ) + jpeg

    transport._dispatch_binary(packet)

    assert len(received) == 1
    assert received[0].frame_type == FRAME_VIDEO
    assert received[0].sequence == 42
    assert received[0].payload == packet


def test_transport_dispatches_face_preview_telemetry_as_sdk_event() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport()
        received = []
        transport.set_callbacks(received.append, lambda _frame: None, lambda: None)
        telemetry = {
            "v": 1,
            "kind": "frame",
            "seq": 42,
            "size": [416, 416],
        }

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(telemetry),
        )

        assert received == [
            {
                "type": "evt.face_tracking.preview.frame",
                "code": 0,
                "data": telemetry,
            }
        ]

    asyncio.run(scenario())
