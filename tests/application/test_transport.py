from __future__ import annotations

import asyncio
import json
import struct

import pytest

from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.errors import WatcheRobotError
from watcherobot.protocol import FLAG_FIRST, FLAG_LAST, FRAME_AUDIO, FRAME_VIDEO, parse_wspk
from watcherobot.runtime.daemon.application.session import ApplicationChannel


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


def test_live_audio_stream_uses_credit_and_finishes_with_last_frame() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        await transport._begin_live_audio_stream(stream_id=9, chunk_bytes=960)
        sequence = await transport._write_live_audio_stream(
            b"\x01\x00" * 600,
            stream_id=9,
            sequence=0,
            chunk_bytes=960,
        )
        await transport._end_live_audio_stream(stream_id=9, sequence=sequence)

        frames = [parse_wspk(packet) for packet in sent]
        assert [frame.sequence for frame in frames] == [0, 1, 2]
        assert frames[0].flags == FLAG_FIRST
        assert frames[0].payload == b"\x01\x00" * 480
        assert frames[1].flags == 0
        assert frames[-1].flags == FLAG_LAST
        assert frames[-1].frame_type == FRAME_AUDIO
        assert frames[-1].payload == b""

    asyncio.run(scenario())


def test_live_audio_stream_does_not_repeat_first_flag_after_sequence_wrap() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        await transport._begin_live_audio_stream(stream_id=10, chunk_bytes=960)
        sequence = await transport._write_live_audio_stream(
            b"\x01\x00" * 960,
            stream_id=10,
            sequence=0xFFFFFFFF,
            chunk_bytes=960,
        )

        frames = [parse_wspk(packet) for packet in sent]
        assert [frame.sequence for frame in frames] == [0xFFFFFFFF, 0]
        assert [frame.flags for frame in frames] == [FLAG_FIRST, 0]
        assert sequence == 1

    asyncio.run(scenario())


def test_live_audio_stream_blocks_on_device_credit_and_wakes_on_status() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        await transport._begin_live_audio_stream(stream_id=12, chunk_bytes=960)
        write = asyncio.create_task(
            transport._write_live_audio_stream(
                b"\x01\x00" * (480 * 5),
                stream_id=12,
                sequence=0,
                chunk_bytes=960,
            )
        )
        await asyncio.sleep(0)
        assert len(sent) == 4
        assert not write.done()

        await transport._update_audio_flow(
            {"stream_id": 12, "reason": "playback", "pending_frames": 0, "queue_depth": 16}
        )
        assert await write == 5
        assert len(sent) == 5

    asyncio.run(scenario())


def test_live_audio_stream_device_failure_wakes_blocked_writer() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)

        async def capture(_channel: ApplicationChannel, _frame: str | bytes) -> None:
            return None

        transport._send = capture  # type: ignore[method-assign]
        await transport._begin_live_audio_stream(stream_id=13, chunk_bytes=960)
        write = asyncio.create_task(
            transport._write_live_audio_stream(
                b"\x01\x00" * (480 * 5),
                stream_id=13,
                sequence=0,
                chunk_bytes=960,
            )
        )
        await asyncio.sleep(0)
        await transport._update_audio_flow(
            {"stream_id": 13, "reason": "playback_write_failed"}
        )
        with pytest.raises(WatcheRobotError, match="playback_write_failed"):
            await write

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
