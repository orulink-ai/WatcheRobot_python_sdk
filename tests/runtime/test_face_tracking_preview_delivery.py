from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from watcherobot.runtime.daemon.connections.registry import ExternalClientRole
from watcherobot.runtime.daemon.preview.delivery import (
    LatestPreviewDelivery,
    PreviewRelayFrame,
)


class FakeClock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeWebSocket:
    def __init__(self, blocked: asyncio.Event | None = None) -> None:
        self.frames: list[str | bytes] = []
        self._blocked = blocked

    async def send(self, frame: str | bytes) -> None:
        if self._blocked is not None:
            await self._blocked.wait()
        self.frames.append(frame)


class FailingWebSocket:
    def __init__(self) -> None:
        self.send_attempts = 0

    async def send(self, _frame: str | bytes) -> None:
        self.send_attempts += 1
        raise RuntimeError("connection is closing")


@dataclass
class FakeConnection:
    websocket: FakeWebSocket
    metadata: dict[str, object] = field(
        default_factory=lambda: {
            "capabilities": ["face_tracking.preview.credit.v1"]
        }
    )


class FakeRegistry:
    def __init__(self, *connections: FakeConnection) -> None:
        self.connections = list(connections)

    def connections_for(self, role: ExternalClientRole) -> list[FakeConnection]:
        assert role is ExternalClientRole.DESKTOP
        return list(self.connections)


def relay_frame(sequence: int, *, completed_at: float = 10.0) -> PreviewRelayFrame:
    return PreviewRelayFrame(
        stream_id=7,
        sequence=sequence,
        telemetry=json.dumps(
            {"v": 1, "kind": "frame", "seq": sequence},
            separators=(",", ":"),
        ),
        image=b"FTW1" + sequence.to_bytes(4, "little"),
        completed_at=completed_at,
    )


async def settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def test_credit_delivery_keeps_one_frame_in_flight_and_skips_to_latest() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        websocket = FakeWebSocket()
        delivery = LatestPreviewDelivery(
            FakeRegistry(FakeConnection(websocket)),
            clock=clock,
        )

        assert delivery.offer(relay_frame(1)) == 1
        await settle()
        assert len(websocket.frames) == 2

        delivery.offer(relay_frame(2))
        delivery.offer(relay_frame(3))
        await settle()
        assert len(websocket.frames) == 2

        clock.now = 10.125
        assert delivery.acknowledge(websocket, sequence=1)
        await settle()

        assert len(websocket.frames) == 4
        telemetry = json.loads(websocket.frames[2])
        assert telemetry["seq"] == 3
        assert telemetry["relay"][1] == 125.0
        assert telemetry["relay"][2] == 1
        assert websocket.frames[3] == relay_frame(3).image
        assert delivery.snapshot()["pending_overwrites"] == 1
        await delivery.stop()

    asyncio.run(scenario())


def test_slow_preview_consumer_does_not_block_another_browser() -> None:
    async def scenario() -> None:
        release_slow = asyncio.Event()
        slow = FakeWebSocket(release_slow)
        fast = FakeWebSocket()
        delivery = LatestPreviewDelivery(
            FakeRegistry(FakeConnection(slow), FakeConnection(fast))
        )

        delivery.offer(relay_frame(11))
        await settle()

        assert slow.frames == []
        assert len(fast.frames) == 2
        release_slow.set()
        await settle()
        assert len(slow.frames) == 2
        await delivery.stop()

    asyncio.run(scenario())


def test_unknown_or_repeated_ack_never_releases_newer_frame() -> None:
    async def scenario() -> None:
        websocket = FakeWebSocket()
        delivery = LatestPreviewDelivery(FakeRegistry(FakeConnection(websocket)))
        delivery.offer(relay_frame(21))
        await settle()

        assert not delivery.acknowledge(websocket, sequence=20)
        delivery.offer(relay_frame(22))
        await settle()
        assert len(websocket.frames) == 2

        assert delivery.acknowledge(websocket, sequence=21)
        await settle()
        assert json.loads(websocket.frames[2])["seq"] == 22
        assert not delivery.acknowledge(websocket, sequence=21)
        await delivery.stop()

    asyncio.run(scenario())


def test_send_failure_discards_pending_frame_instead_of_retrying_backlog() -> None:
    async def scenario() -> None:
        websocket = FailingWebSocket()
        connection = FakeConnection(websocket)  # type: ignore[arg-type]
        delivery = LatestPreviewDelivery(FakeRegistry(connection))

        delivery.offer(relay_frame(31))
        delivery.offer(relay_frame(32))
        await settle()

        assert websocket.send_attempts == 1
        assert delivery.snapshot()["send_errors"] == 1
        assert delivery.snapshot()["active_consumers"] == 0
        await delivery.stop()

    asyncio.run(scenario())


def test_connection_loss_does_not_flush_pending_legacy_frame() -> None:
    async def scenario() -> None:
        release = asyncio.Event()
        websocket = FakeWebSocket(release)
        connection = FakeConnection(websocket, metadata={})
        delivery = LatestPreviewDelivery(FakeRegistry(connection))

        delivery.offer(relay_frame(41))
        delivery.offer(relay_frame(42))
        await settle()
        delivery.connection_lost(websocket)
        await settle()
        release.set()
        await settle()

        assert websocket.frames == []
        assert delivery.snapshot()["active_consumers"] == 0
        await delivery.stop()

    asyncio.run(scenario())
