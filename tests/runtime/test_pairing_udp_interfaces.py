from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Awaitable, Callable, Sequence

import pytest

from watcherobot.runtime.daemon.pairing.protocol import PairAccept, encode_udp_message
from watcherobot.runtime.daemon.pairing.session import (
    DevicePairingSession,
    DevicePairingState,
)
from watcherobot.runtime.daemon.pairing.udp import (
    PairingUdpInterface,
    PairingUdpService,
    collect_lan_interfaces,
)


DAEMON_ID = "f730f29e670c49f7a3320c4314eb9805"
REQUEST_ID = "21a9dbf05ea3443480e62076f79a3b12"
SESSION_TOKEN = (
    "f84a1e16ce6f35f14d167f227a93ea93"
    "d1a9c4d9eb5517112030f2839d57ae4b"
)

DatagramHandler = Callable[
    [bytes, tuple[str, int], PairingUdpInterface],
    Awaitable[bool],
]


class FakePairingUdpChannel:
    def __init__(
        self,
        interface: PairingUdpInterface,
        *,
        bound_port: int,
        datagram_handler: DatagramHandler,
    ) -> None:
        self.interface = interface
        self.bound_port = bound_port
        self.datagram_handler = datagram_handler
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
    def __init__(self, *, failing_ips: Sequence[str] = ()) -> None:
        self.failing_ips = set(failing_ips)
        self.created: list[FakePairingUdpChannel] = []

    async def __call__(
        self,
        interface: PairingUdpInterface,
        port: int,
        datagram_handler: DatagramHandler,
    ) -> FakePairingUdpChannel:
        if interface.local_ip in self.failing_ips:
            raise OSError(f"cannot bind {interface.local_ip}")
        channel = FakePairingUdpChannel(
            interface,
            bound_port=port or 40000 + len(self.created),
            datagram_handler=datagram_handler,
        )
        self.created.append(channel)
        return channel


def make_session() -> DevicePairingSession:
    return DevicePairingSession(
        daemon_instance_id=DAEMON_ID,
        request_id_factory=lambda: REQUEST_ID,
    )


def lan(
    name: str,
    local_ip: str,
    broadcast_ip: str,
    *,
    netmask: str = "255.255.255.0",
) -> PairingUdpInterface:
    return PairingUdpInterface(
        interface_name=name,
        local_ip=local_ip,
        netmask=netmask,
        broadcast_ip=broadcast_ip,
    )


def test_collect_lan_interfaces_keeps_same_broadcast_on_distinct_interfaces() -> None:
    interfaces = collect_lan_interfaces(
        interface_addresses={
            "Ethernet": [
                {
                    "family": socket.AF_INET,
                    "address": "192.168.1.20",
                    "netmask": "255.255.255.0",
                }
            ],
            "Wi-Fi": [
                {
                    "family": socket.AF_INET,
                    "address": "192.168.1.21",
                    "netmask": "255.255.255.0",
                }
            ],
            "WSL": [
                {
                    "family": socket.AF_INET,
                    "address": "172.22.48.1",
                    "netmask": "255.255.240.0",
                }
            ],
            "PointToPoint": [
                {
                    "family": socket.AF_INET,
                    "address": "10.1.1.1",
                    "netmask": "255.255.255.255",
                }
            ],
        },
        interface_stats={
            "Ethernet": {"isup": True},
            "Wi-Fi": {"isup": True},
            "WSL": {"isup": True},
            "PointToPoint": {"isup": True},
        },
    )

    assert interfaces == (
        lan("Ethernet", "192.168.1.20", "192.168.1.255"),
        lan("Wi-Fi", "192.168.1.21", "192.168.1.255"),
        lan(
            "WSL",
            "172.22.48.1",
            "172.22.63.255",
            netmask="255.255.240.0",
        ),
    )
    assert all(item.broadcast_ip != "255.255.255.255" for item in interfaces)


def test_udp_service_reconciles_added_removed_and_changed_interfaces() -> None:
    async def scenario() -> None:
        snapshots = [
            (lan("Ethernet", "192.168.31.99", "192.168.31.255"),),
            (
                lan("Ethernet", "192.168.31.99", "192.168.31.255"),
                lan("Wi-Fi", "192.168.1.20", "192.168.1.255"),
            ),
            (lan("Wi-Fi", "192.168.1.21", "192.168.1.255"),),
        ]
        snapshot_index = 0
        factory = FakeChannelFactory()
        session = make_session()
        service = PairingUdpService(
            session=session,
            interface_provider=lambda: snapshots[snapshot_index],
            channel_factory=factory,
        )

        await service.start()
        assert service.active_interfaces == snapshots[0]
        ethernet_channel = factory.created[0]

        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=10.0,
        )
        snapshot_index = 1
        assert await service.broadcast_once()
        assert service.active_interfaces == snapshots[1]
        wifi_old_channel = factory.created[1]
        assert len(ethernet_channel.broadcasts) == 1
        assert len(wifi_old_channel.broadcasts) == 1

        snapshot_index = 2
        assert await service.broadcast_once()
        assert service.active_interfaces == snapshots[2]
        wifi_new_channel = factory.created[2]
        assert ethernet_channel.closed
        assert wifi_old_channel.closed
        assert not wifi_new_channel.closed
        assert len(wifi_new_channel.broadcasts) == 1

        await service.stop()
        assert wifi_new_channel.closed

    asyncio.run(scenario())


def test_udp_service_discovers_interface_added_after_daemon_start() -> None:
    async def scenario() -> None:
        current: tuple[PairingUdpInterface, ...] = ()
        factory = FakeChannelFactory()
        session = make_session()
        service = PairingUdpService(
            session=session,
            interface_provider=lambda: current,
            channel_factory=factory,
        )

        await service.start()
        assert service.active_interfaces == ()

        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=10.0,
        )
        current = (lan("Wi-Fi", "192.168.1.20", "192.168.1.255"),)

        assert await service.broadcast_once()
        assert service.active_interfaces == current
        assert len(factory.created[0].broadcasts) == 1
        await service.stop()

    asyncio.run(scenario())


def test_udp_service_keeps_healthy_interface_when_another_bind_fails() -> None:
    async def scenario() -> None:
        interfaces = (
            lan("Ethernet", "192.168.31.99", "192.168.31.255"),
            lan("Wi-Fi", "192.168.1.20", "192.168.1.255"),
        )
        factory = FakeChannelFactory(failing_ips=("192.168.1.20",))
        session = make_session()
        service = PairingUdpService(
            session=session,
            interface_provider=lambda: interfaces,
            channel_factory=factory,
        )

        await service.start()
        assert service.active_interfaces == (interfaces[0],)
        assert service.channel_errors == {
            interfaces[1]: "cannot bind 192.168.1.20"
        }

        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=10.0,
        )
        assert await service.broadcast_once()
        assert len(factory.created[0].broadcasts) == 1
        await service.stop()

    asyncio.run(scenario())


def test_udp_service_start_fails_when_every_visible_interface_cannot_bind() -> None:
    async def scenario() -> None:
        wifi = lan("Wi-Fi", "192.168.1.20", "192.168.1.255")
        service = PairingUdpService(
            session=make_session(),
            interface_provider=lambda: (wifi,),
            channel_factory=FakeChannelFactory(
                failing_ips=("192.168.1.20",)
            ),
        )

        with pytest.raises(
            RuntimeError,
            match="no pairing UDP interface could be bound",
        ):
            await service.start()
        with pytest.raises(
            RuntimeError,
            match="pairing UDP service is not running",
        ):
            _ = service.bound_port

    asyncio.run(scenario())


def test_udp_service_reopens_channel_after_broadcast_failure() -> None:
    class FailingBroadcastChannel(FakePairingUdpChannel):
        def send_broadcast(self, data: bytes) -> None:
            raise OSError("network path unavailable")

    class RecoveringFactory(FakeChannelFactory):
        async def __call__(
            self,
            interface: PairingUdpInterface,
            port: int,
            datagram_handler: DatagramHandler,
        ) -> FakePairingUdpChannel:
            if not self.created:
                channel: FakePairingUdpChannel = FailingBroadcastChannel(
                    interface,
                    bound_port=port,
                    datagram_handler=datagram_handler,
                )
            else:
                channel = FakePairingUdpChannel(
                    interface,
                    bound_port=port,
                    datagram_handler=datagram_handler,
                )
            self.created.append(channel)
            return channel

    async def scenario() -> None:
        wifi = lan("Wi-Fi", "192.168.1.20", "192.168.1.255")
        factory = RecoveringFactory()
        session = make_session()
        service = PairingUdpService(
            session=session,
            interface_provider=lambda: (wifi,),
            channel_factory=factory,
        )
        await service.start()
        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=10.0,
        )

        assert not await service.broadcast_once()
        assert factory.created[0].closed
        assert service.active_interfaces == ()

        assert await service.broadcast_once()
        assert len(factory.created) == 2
        assert len(factory.created[1].broadcasts) == 1
        await service.stop()

    asyncio.run(scenario())


def test_pair_accept_and_cancel_stay_on_the_receiving_interface() -> None:
    async def scenario() -> None:
        ethernet = lan("Ethernet", "192.168.31.99", "192.168.31.255")
        wifi = lan("Wi-Fi", "192.168.1.20", "192.168.1.255")
        factory = FakeChannelFactory()
        events: list[str] = []
        session = make_session()
        service = PairingUdpService(
            session=session,
            interface_provider=lambda: (ethernet, wifi),
            channel_factory=factory,
            clock=lambda: 12.0,
            event_logger=events.append,
        )
        await service.start()
        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=10.0,
        )
        response = PairAccept(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            target_mode="desktop_link",
            session_token=SESSION_TOKEN,
        )

        assert await service.handle_datagram(
            encode_udp_message(response),
            ("192.168.1.88", 37021),
            interface=wifi,
        )
        assert session.state is DevicePairingState.CONNECTING
        assert service.last_receive_context == {
            "peer_ip": "192.168.1.88",
            "local_ip": "192.168.1.20",
            "interface_name": "Wi-Fi",
        }
        assert any(
            "peer=192.168.1.88" in event
            and "local=192.168.1.20" in event
            and "interface=Wi-Fi" in event
            for event in events
        )

        assert await service.cancel_pairing()
        ethernet_channel, wifi_channel = factory.created
        assert ethernet_channel.unicasts == []
        assert len(wifi_channel.unicasts) == 1
        payload, address = wifi_channel.unicasts[0]
        assert address == ("192.168.1.88", 37021)
        assert json.loads(payload)["type"] == "pair.cancel"
        await service.stop()

    asyncio.run(scenario())
