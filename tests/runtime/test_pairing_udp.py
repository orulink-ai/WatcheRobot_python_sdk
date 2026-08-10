from __future__ import annotations

import asyncio
import json

from watcherobot.runtime.daemon.pairing.protocol import (
    PairAccept,
    PairBusy,
    encode_udp_message,
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
REQUEST_ID = "21a9dbf05ea3443480e62076f79a3b12"
SESSION_TOKEN = (
    "f84a1e16ce6f35f14d167f227a93ea93"
    "d1a9c4d9eb5517112030f2839d57ae4b"
)
ETHERNET = PairingUdpInterface(
    interface_name="Ethernet",
    local_ip="192.168.31.99",
    netmask="255.255.255.0",
    broadcast_ip="192.168.31.255",
)
WIFI = PairingUdpInterface(
    interface_name="Wi-Fi",
    local_ip="192.168.1.20",
    netmask="255.255.255.0",
    broadcast_ip="192.168.1.255",
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

    async def __call__(
        self,
        interface: PairingUdpInterface,
        port: int,
        _datagram_handler,
    ) -> FakePairingUdpChannel:
        channel = FakePairingUdpChannel(interface, port)
        self.channels[interface] = channel
        return channel


def make_session() -> DevicePairingSession:
    return DevicePairingSession(
        daemon_instance_id=DAEMON_ID,
        request_id_factory=lambda: REQUEST_ID,
    )


def test_udp_service_broadcasts_daemon_owned_pair_request_on_each_interface() -> None:
    async def scenario() -> None:
        session = make_session()
        request = session.start_pairing(
            pairing_code="123456",
            target_mode="python_sdk",
            websocket_port=8765,
            now=10.0,
        )
        states: list[str] = []
        factory = FakeChannelFactory()
        service = PairingUdpService(
            session=session,
            clock=lambda: 10.0,
            interface_provider=lambda: (ETHERNET, WIFI),
            channel_factory=factory,
            state_listener=lambda snapshot: states.append(
                str(snapshot["state"])
            ),
        )
        await service.start()

        assert await service.broadcast_once() is True
        assert all(
            len(factory.channels[interface].broadcasts) == 1
            for interface in (ETHERNET, WIFI)
        )
        datagram = factory.channels[ETHERNET].broadcasts[0]
        assert json.loads(datagram) == {
            "type": "pair.request",
            "protocol": "watcher-lan-pairing",
            "version": "1.0",
            "request_id": request.request_id,
            "daemon_instance_id": DAEMON_ID,
            "pairing_code": "123456",
            "target_mode": "python_sdk",
            "websocket_port": 8765,
        }
        assert states == []
        await service.stop()

    asyncio.run(scenario())


def test_udp_service_accepts_first_matching_device_and_stops_broadcasting() -> None:
    async def scenario() -> None:
        session = make_session()
        session.start_pairing(
            pairing_code="123456",
            target_mode="python_sdk",
            websocket_port=8765,
            now=10.0,
        )
        states: list[str] = []
        factory = FakeChannelFactory()
        service = PairingUdpService(
            session=session,
            clock=lambda: 12.0,
            interface_provider=lambda: (WIFI,),
            channel_factory=factory,
            state_listener=lambda snapshot: states.append(
                str(snapshot["state"])
            ),
        )
        await service.start()
        response = PairAccept(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            target_mode="python_sdk",
            session_token=SESSION_TOKEN,
        )

        assert await service.handle_datagram(
            encode_udp_message(response),
            ("192.168.1.25", 37021),
            interface=WIFI,
        )
        assert session.state is DevicePairingState.CONNECTING
        assert session.expected_peer_ip == "192.168.1.25"
        assert states == ["connecting"]
        assert await service.broadcast_once() is False

        assert not await service.handle_datagram(
            encode_udp_message(response),
            ("192.168.1.26", 37021),
            interface=WIFI,
        )
        assert session.expected_peer_ip == "192.168.1.25"
        await service.stop()

    asyncio.run(scenario())


def test_udp_service_handles_busy_invalid_and_cancel_without_leaking_slot() -> None:
    async def scenario() -> None:
        session = make_session()
        session.start_pairing(
            pairing_code="123456",
            target_mode="python_sdk",
            websocket_port=8765,
            now=10.0,
        )
        states: list[dict[str, object]] = []
        factory = FakeChannelFactory()
        service = PairingUdpService(
            session=session,
            clock=lambda: 12.0,
            interface_provider=lambda: (WIFI,),
            channel_factory=factory,
            state_listener=lambda snapshot: states.append(dict(snapshot)),
        )
        await service.start()

        assert not await service.handle_datagram(
            b'{"type":"SDK_DISCOVER"}',
            ("192.168.1.99", 37021),
            interface=WIFI,
        )
        assert session.state is DevicePairingState.DISCOVERING

        busy = PairBusy(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            reason="device_session_active",
        )
        assert await service.handle_datagram(
            encode_udp_message(busy),
            ("192.168.1.25", 37021),
            interface=WIFI,
        )
        assert session.state is DevicePairingState.IDLE
        assert states[-1]["last_error"] == "device_busy"

        session.start_pairing(
            pairing_code="123456",
            target_mode="python_sdk",
            websocket_port=8765,
            now=20.0,
        )
        accept = PairAccept(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            target_mode="python_sdk",
            session_token=SESSION_TOKEN,
        )
        await service.handle_datagram(
            encode_udp_message(accept),
            ("192.168.1.25", 37021),
            interface=WIFI,
        )
        assert await service.cancel_pairing() is True
        assert session.state is DevicePairingState.IDLE
        cancel_payload, cancel_address = factory.channels[WIFI].unicasts[-1]
        assert cancel_address == ("192.168.1.25", 37021)
        assert json.loads(cancel_payload)["type"] == "pair.cancel"
        assert json.loads(cancel_payload)["session_token"] == SESSION_TOKEN
        await service.stop()

    asyncio.run(scenario())


def test_udp_service_expires_discovery_and_notifies_state() -> None:
    async def scenario() -> None:
        session = make_session()
        session.start_pairing(
            pairing_code="123456",
            target_mode="python_sdk",
            websocket_port=8765,
            now=10.0,
        )
        states: list[dict[str, object]] = []
        service = PairingUdpService(
            session=session,
            clock=lambda: 20.0,
            interface_provider=lambda: (),
            channel_factory=FakeChannelFactory(),
            state_listener=lambda snapshot: states.append(dict(snapshot)),
        )
        await service.start()

        assert await service.expire_once() is True
        assert session.state is DevicePairingState.IDLE
        assert states[-1]["last_error"] == "pairing_not_found"
        await service.stop()

    asyncio.run(scenario())
