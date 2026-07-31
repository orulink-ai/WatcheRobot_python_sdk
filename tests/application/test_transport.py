from __future__ import annotations

import asyncio
import json
import threading

from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.protocol import (
    FLAG_FIRST,
    FLAG_KEYFRAME,
    FLAG_LAST,
    FRAME_IMAGE,
    FRAME_VIDEO,
    parse_wspk,
)
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


def test_display_image_uses_one_complete_wspk_frame() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        await transport._send_display_image(b"jpeg", stream_id=41)

        frame = parse_wspk(sent[0])
        assert frame.frame_type == FRAME_IMAGE
        assert frame.flags == FLAG_FIRST | FLAG_LAST | FLAG_KEYFRAME
        assert frame.stream_id == 41
        assert frame.sequence == 0
        assert frame.payload == b"jpeg"

    asyncio.run(scenario())


def test_display_stream_paces_frames_and_sends_terminal_marker() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        count = await transport._send_display_stream(
            [b"one", b"two"],
            stream_id=42,
            fps=10.0,
        )

        frames = [parse_wspk(packet) for packet in sent]
        assert count == 2
        assert [frame.frame_type for frame in frames] == [
            FRAME_VIDEO,
            FRAME_VIDEO,
            FRAME_VIDEO,
        ]
        assert frames[0].flags == FLAG_FIRST | FLAG_KEYFRAME
        assert frames[1].flags == FLAG_KEYFRAME
        assert frames[2].flags == FLAG_LAST
        assert [frame.sequence for frame in frames] == [0, 1, 2]
        assert [frame.payload for frame in frames] == [b"one", b"two", b""]

    asyncio.run(scenario())


def test_display_stream_reads_blocking_iterators_off_the_transport_loop() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        loop_thread = threading.get_ident()
        iterator_threads: list[int] = []

        def frames():
            iterator_threads.append(threading.get_ident())
            yield b"one"

        async def capture(
            _channel: ApplicationChannel,
            _frame: str | bytes,
        ) -> None:
            return None

        transport._send = capture  # type: ignore[method-assign]
        await transport._send_display_stream(
            frames(),
            stream_id=43,
            fps=10.0,
        )

        assert iterator_threads
        assert iterator_threads[0] != loop_thread

    asyncio.run(scenario())


def test_empty_display_stream_sends_no_terminal_for_an_unknown_stream() -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []

        async def capture(
            _channel: ApplicationChannel,
            frame: str | bytes,
        ) -> None:
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]
        count = await transport._send_display_stream(
            [],
            stream_id=44,
            fps=10.0,
        )

        assert count == 0
        assert sent == []

    asyncio.run(scenario())
