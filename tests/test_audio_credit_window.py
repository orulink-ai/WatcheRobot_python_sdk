from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from watcherobot._internal.audio_credit import AudioInFlightWindow
from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.desktop_session import DesktopRobotSession


@pytest.mark.parametrize("desktop", [False, True])
def test_repeated_status_does_not_regrant_inflight_audio(desktop: bool) -> None:
    async def exercise() -> None:
        transport = (
            DesktopRobotSession("ws://127.0.0.1:1")
            if desktop else DaemonApplicationTransport(command_timeout=1.0)
        )
        packets: list[bytes] = []

        async def capture(*args: object) -> None:
            assert isinstance(args[-1], bytes)
            packets.append(args[-1])

        transport._send = capture  # type: ignore[method-assign]
        sender = transport._send_audio if desktop else transport._send_audio_stream  # type: ignore[union-attr]
        task = asyncio.create_task(sender(bytes(4096 * 64), 7, 4096))

        async def settle() -> None:
            for _ in range(100):
                await asyncio.sleep(0)

        async def status(ack: int, pending: int) -> None:
            await transport._update_audio_flow({
                "stream_id": 7, "expected_rx_seq": ack,
                "pending_frames": pending, "queue_depth": 64, "playing": True,
            })
            await settle()

        try:
            await settle()
            assert len(packets) == 4
            await status(1, 1)
            assert len(packets) == 9  # one acknowledged plus eight in flight
            for _ in range(3):
                await status(1, 1)
            assert len(packets) == 9
            await status(5, 5)
            assert len(packets) == 13
            await status(4, 0)  # stale snapshot cannot reissue a credit
            await status(1000, 0)  # cannot acknowledge unsent packets
            await status(True, 0)  # bool is not a sequence number
            assert len(packets) == 13
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(exercise())


def test_legacy_credit_fallback_stops_after_receive_watermark() -> None:
    window = AudioInFlightWindow()
    for _ in range(4):
        window.reserve()
    assert window.available({}, 8) == 8
    assert window.available({"expected_rx_seq": 1}, 8) == 5
    assert window.available({}, 8) is None
    assert window.available({"expected_rx_seq": 4}, 8) == 8


def test_full_device_queue_and_new_stream_window() -> None:
    window = AudioInFlightWindow()
    for _ in range(8):
        window.reserve()
    assert window.available({"expected_rx_seq": 4}, 0) == 0
    assert window.available({"expected_rx_seq": 4}, 8) == 4
    assert window.available({"expected_rx_seq": -1}, 8) is None
    assert AudioInFlightWindow().available({"expected_rx_seq": 0}, 8) == 8
