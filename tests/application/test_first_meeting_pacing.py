import asyncio, time
import pytest
import watcherobot.application.transport as transport_module
from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.runtime.daemon.application.session import ApplicationChannel
import json
def test_audio_stream_prefills_the_device_start_buffer_without_bursting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=1.0)
        sent: list[bytes] = []
        sent_at: list[float] = []

        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)
            sent_at.append(time.monotonic())

        transport._send = capture  # type: ignore[method-assign]
        monkeypatch.setattr(
            transport_module,
            "AUDIO_PCM_BYTES_PER_SECOND",
            4096 / 0.03,
        )
        monkeypatch.setattr(
            transport_module,
            "AUDIO_FALLBACK_PACKET_SECONDS",
            1.0,
        )
        task = asyncio.create_task(
            transport._send_audio_stream(b"\x00" * (4096 * 17), stream_id=9, chunk_bytes=4096)
        )

        for _ in range(100):
            if len(sent) == 16:
                break
            await asyncio.sleep(0.01)
        assert len(sent) == 16

        await transport._on_frame(
            ApplicationChannel.DEVICE,
            json.dumps(
                {
                    "type": "evt.audio.buffer_status",
                    "code": 0,
                    "data": {
                        "stream_id": 9,
                        "playing": True,
                        "pending_frames": 7,
                        "queue_depth": 16,
                        "start_buffer_frames": 16,
                    },
                }
            ),
        )
        await asyncio.wait_for(task, timeout=1.0)
        assert len(sent) == 17
        # Windows' default asyncio timer coalesces short sleeps, but the full
        # prebuffer must still span multiple scheduler quanta rather than one
        # immediate 16-frame burst.
        assert sent_at[15] - sent_at[0] >= 0.2
        assert sent_at[-1] - sent_at[0] < 0.6

    asyncio.run(scenario())
def test_audio_stream_uses_realtime_pacing_when_device_credit_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        transport = DaemonApplicationTransport(command_timeout=0.2)
        sent: list[bytes] = []

        monkeypatch.setattr(
            transport_module,
            "AUDIO_PCM_BYTES_PER_SECOND",
            4096 / 0.001,
        )
        monkeypatch.setattr(
            transport_module,
            "AUDIO_FALLBACK_PACKET_SECONDS",
            0.005,
        )
        async def capture(channel: ApplicationChannel, frame: str | bytes) -> None:
            assert channel is ApplicationChannel.DEVICE
            assert isinstance(frame, bytes)
            sent.append(frame)

        transport._send = capture  # type: ignore[method-assign]

        await asyncio.wait_for(
            transport._send_audio_stream(
                b"\x00" * (4096 * 17),
                stream_id=10,
                chunk_bytes=4096,
            ),
            timeout=0.5,
        )

        assert len(sent) == 17

    asyncio.run(scenario())
@pytest.mark.parametrize('watermark_during_wait', [False, True])
def test_watermark_credit_wait_uses_command_timeout(monkeypatch, watermark_during_wait):
    async def scenario():
        transport = DaemonApplicationTransport(command_timeout=0.3)
        transport._audio_credit_condition = asyncio.Condition()
        transport._audio_flow_stream_id = 19
        transport._audio_credits = 0
        monkeypatch.setattr(transport_module, 'AUDIO_FALLBACK_PACKET_SECONDS', 0.02)
        if not watermark_during_wait:
            transport._audio_inflight.available({'expected_rx_seq': 0}, 0)
        task = asyncio.create_task(transport._take_audio_credit(19))
        await asyncio.sleep(0.005)
        if watermark_during_wait:
            await transport._update_audio_flow({'stream_id': 19, 'expected_rx_seq': 0, 'pending_frames': 16, 'queue_depth': 16})
        await asyncio.sleep(0.06)
        assert not task.done(), 'negotiated flow must wait for real credit'
        assert transport._audio_inflight.reserved == 0
        await transport._update_audio_flow({'stream_id': 19, 'expected_rx_seq': 0, 'pending_frames': 0, 'queue_depth': 16})
        await task
        assert transport._audio_inflight.reserved == 1
    asyncio.run(scenario())
