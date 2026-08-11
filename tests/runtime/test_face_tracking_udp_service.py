from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

from watcherobot.runtime.daemon.connections.registry import ExternalClientRole
from watcherobot.runtime.daemon.pairing.protocol import HardwareHello, PairAccept
from watcherobot.runtime.daemon.pairing.session import DevicePairingSession
from watcherobot.runtime.daemon.preview.udp_protocol import (
    build_preview_bundle,
    encode_preview_datagrams,
)
from watcherobot.runtime.daemon.preview.udp_service import FaceTrackingUdpPreviewService


DAEMON_ID = "f730f29e670c49f7a3320c4314eb9805"
REQUEST_ID = "21a9dbf05ea3443480e62076f79a3b12"
TOKEN = "ab" * 32
PEER_IP = "192.168.1.25"


class FakeRegistry:
    def __init__(self) -> None:
        self.frames: list[tuple[ExternalClientRole, str | bytes]] = []

    async def send_to_role(self, role: ExternalClientRole, frame: str | bytes) -> int:
        self.frames.append((role, frame))
        return 1


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def connected_session() -> DevicePairingSession:
    session = DevicePairingSession(
        daemon_instance_id=DAEMON_ID, request_id_factory=lambda: REQUEST_ID
    )
    session.start_pairing(
        pairing_code="123456",
        target_mode="python_sdk",
        websocket_port=8765,
        now=1.0,
    )
    session.accept_device(
        PairAccept(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            target_mode="python_sdk",
            session_token=TOKEN,
        ),
        peer_ip=PEER_IP,
        now=2.0,
    )
    session.connect_device(
        HardwareHello(
            pair_request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            session_token=TOKEN,
            mode="python_sdk",
        ),
        peer_ip=PEER_IP,
        now=3.0,
    )
    return session


def packets(sequence: int = 9) -> list[bytes]:
    telemetry = '{"v":1,"kind":"frame","seq":%d}' % sequence
    image = b"FTW1" + bytes(20) + b"jpeg"
    key = hmac.new(TOKEN.encode("ascii"), b"face-preview-v1", hashlib.sha256).digest()
    return encode_preview_datagrams(
        build_preview_bundle(telemetry, image),
        session_key=key,
        stream_id=123,
        sequence=sequence,
        max_datagram_size=64,
    )


def test_service_rejects_wrong_source_and_publishes_pair_to_desktop() -> None:
    async def scenario() -> None:
        registry = FakeRegistry()
        clock = FakeClock(10.0)
        service = FaceTrackingUdpPreviewService(
            session=connected_session(), registry=registry, port=0, clock=clock
        )
        await service.start()
        for packet in packets():
            service.handle_datagram(packet, ("192.168.1.99", 50000))
        assert service.stats.wrong_source_datagrams == len(packets())
        for packet in reversed(packets()):
            service.handle_datagram(packet, (PEER_IP, 50000))
        clock.now = 10.025
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert registry.frames == [
            (
                ExternalClientRole.DESKTOP,
                '{"v":1,"kind":"frame","seq":9,"relay":[10000,25.0]}',
            ),
            (ExternalClientRole.DESKTOP, b"FTW1" + bytes(20) + b"jpeg"),
        ]
        assert service.stats.published_frames == 1
        assert service.stats.feedback_sent == 1
        assert service.stats.feedback_errors == 0
        assert service.bound_port > 0
        await service.stop()

    asyncio.run(scenario())


def test_service_rejects_bad_authentication_and_inactive_session() -> None:
    async def scenario() -> None:
        session = connected_session()
        service = FaceTrackingUdpPreviewService(
            session=session, registry=FakeRegistry(), port=0
        )
        await service.start()
        bad = bytearray(packets()[0])
        bad[32] ^= 1
        service.handle_datagram(bytes(bad), (PEER_IP, 50000))
        assert service.stats.invalid_datagrams == 1
        session.release()
        service.handle_datagram(packets()[0], (PEER_IP, 50000))
        assert service.stats.inactive_session_datagrams == 1
        await service.stop()

    asyncio.run(scenario())


def test_service_can_publish_to_managed_application_sink() -> None:
    async def scenario() -> None:
        published: list[str | bytes] = []

        async def publish(frame: str | bytes) -> int:
            published.append(frame)
            return 1

        service = FaceTrackingUdpPreviewService(
            session=connected_session(),
            registry=FakeRegistry(),
            publisher=publish,
            port=0,
        )
        await service.start()
        for packet in packets(sequence=17):
            service.handle_datagram(packet, (PEER_IP, 50000))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert len(published) == 2
        assert isinstance(published[0], str)
        assert json.loads(published[0])["seq"] == 17
        assert published[1] == b"FTW1" + bytes(20) + b"jpeg"
        await service.stop()

    asyncio.run(scenario())
