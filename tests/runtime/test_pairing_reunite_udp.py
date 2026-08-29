"""UDP transport behaviour for the reunite scan: broadcast, unicast, accept."""

from __future__ import annotations

import asyncio
import json

from watcherobot.runtime.daemon.pairing.protocol import (
    LinkReuniteAccept,
    reunite_response_mac,
)
from watcherobot.runtime.daemon.pairing.session import (
    DevicePairingSession,
    DevicePairingState,
)
from watcherobot.runtime.daemon.pairing.udp import (
    PairingUdpInterface,
    PairingUdpService,
)

DAEMON_ID = "f730f29e670c49f7a3320c4314eb9805"
REUNITE_REQUEST_ID = "5256e0a52e79fcccc45eb8e91be4a5fe"
NONCE = "7ba1f4a9dd93cdfca35eebf2fa99e0ff"
BINDING_SECRET = "f8e64ced0ed799c6f1a46a0852fdaf0f80babf6b223da440127c4a8c7a8c03dc"
REUNITE_TOKEN = "d" * 64
LAST_PEER_IP = "192.168.31.7"

ETHERNET = PairingUdpInterface(
    interface_name="Ethernet",
    local_ip="192.168.31.99",
    netmask="255.255.255.0",
    broadcast_ip="192.168.31.255",
)


class FakePairingUdpChannel:
    def __init__(self, interface: PairingUdpInterface, bound_port: int) -> None:
        self.interface = interface
        self.bound_port = bound_port or 43000
        self.broadcasts: list[bytes] = []
        self.unicasts: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def send_broadcast(self, data: bytes) -> None:
        self.broadcasts.append(data)

    def send_unicast(self, data: bytes, address: tuple[str, int]) -> None:
        self.unicasts.append((data, address))

    def close(self) -> None:
        self.closed = True


class FakeChannelFactory:
    def __init__(self) -> None:
        self.channels: dict[PairingUdpInterface, FakePairingUdpChannel] = {}

    async def __call__(self, interface, port, _datagram_handler) -> FakePairingUdpChannel:
        channel = FakePairingUdpChannel(interface, port)
        self.channels[interface] = channel
        return channel


def make_service(**kwargs) -> tuple[DevicePairingSession, PairingUdpService, FakeChannelFactory]:
    session = DevicePairingSession(daemon_instance_id=DAEMON_ID)
    factory = FakeChannelFactory()
    service = PairingUdpService(
        session=session,
        clock=kwargs.pop("clock", lambda: 100.0),
        interface_provider=lambda: (ETHERNET,),
        channel_factory=factory,
        **kwargs,
    )
    return session, service, factory


def start_scan(session: DevicePairingSession) -> None:
    session.start_reunite_scan(
        request_id=REUNITE_REQUEST_ID,
        nonce=NONCE,
        target_mode="desktop_link",
        websocket_port=8765,
        binding_secret=BINDING_SECRET,
        now=100.0,
    )


def make_accept(**overrides) -> LinkReuniteAccept:
    values = {
        "request_id": REUNITE_REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "nonce": NONCE,
        "target_mode": "desktop_link",
        "response_mac": reunite_response_mac(
            BINDING_SECRET,
            request_id=REUNITE_REQUEST_ID,
            nonce=NONCE,
            daemon_instance_id=DAEMON_ID,
            target_mode="desktop_link",
        ),
        "session_token": REUNITE_TOKEN,
    }
    values.update(overrides)
    return LinkReuniteAccept(**values)


def test_broadcast_loop_emits_reunite_request_on_scan() -> None:
    async def scenario() -> None:
        session, service, factory = make_service()
        start_scan(session)
        await service.start()

        assert await service.broadcast_once() is True
        datagram = factory.channels[ETHERNET].broadcasts[-1]
        assert json.loads(datagram.decode("utf-8")) == {
            "type": "link.reunite.request",
            "protocol": "watcher-lan-pairing",
            "version": "1.1",
            "request_id": REUNITE_REQUEST_ID,
            "daemon_instance_id": DAEMON_ID,
            "nonce": NONCE,
            "target_mode": "desktop_link",
            "websocket_port": 8765,
        }
        await service.stop()

    asyncio.run(scenario())


def test_scan_unicasts_remembered_device_when_peer_ip_given() -> None:
    async def scenario() -> None:
        session, service, factory = make_service()
        start_scan(session)
        await service.start()
        service.activate(peer_ip=LAST_PEER_IP)

        assert await service.broadcast_once() is True
        unicasts = list(factory.channels[ETHERNET].unicasts)
        assert (json.loads(unicasts[0][0].decode("utf-8"))["type"]) == "link.reunite.request"
        assert unicasts[0][1] == (LAST_PEER_IP, 37021)
        await service.stop()

    asyncio.run(scenario())


def test_valid_accept_moves_session_to_connecting() -> None:
    async def scenario() -> None:
        session, service, _factory = make_service()
        start_scan(session)
        await service.start()

        accepted = await service.handle_datagram(
            json.dumps(
                {
                    "type": "link.reunite.accept",
                    "protocol": "watcher-lan-pairing",
                    "version": "1.1",
                    "request_id": REUNITE_REQUEST_ID,
                    "daemon_instance_id": DAEMON_ID,
                    "nonce": NONCE,
                    "target_mode": "desktop_link",
                    "response_mac": make_accept().response_mac,
                    "session_token": REUNITE_TOKEN,
                }
            ).encode("utf-8"),
            (LAST_PEER_IP, 40000),
        )

        assert accepted is True
        assert session.state is DevicePairingState.CONNECTING
        assert session.expected_peer_ip == LAST_PEER_IP
        await service.stop()

    asyncio.run(scenario())


def test_bad_mac_is_swallowed_and_scan_continues() -> None:
    async def scenario() -> None:
        session, service, _factory = make_service()
        start_scan(session)
        await service.start()

        accepted = await service.handle_datagram(
            encode_accept(response_mac="e" * 64),
            (LAST_PEER_IP, 40000),
        )
        still_scanning = await service.broadcast_once()

        assert accepted is False
        assert still_scanning is True
        assert session.state is DevicePairingState.DISCOVERING
        await service.stop()

    asyncio.run(scenario())


def encode_accept(**overrides) -> bytes:
    values = {
        "type": "link.reunite.accept",
        "protocol": "watcher-lan-pairing",
        "version": "1.1",
        "request_id": REUNITE_REQUEST_ID,
        "daemon_instance_id": DAEMON_ID,
        "nonce": NONCE,
        "target_mode": "desktop_link",
        "response_mac": make_accept().response_mac,
        "session_token": REUNITE_TOKEN,
    }
    values.update(overrides)
    return json.dumps(values).encode("utf-8")
