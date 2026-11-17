"""Real socket regression for discovery after an unreachable stale peer."""

from __future__ import annotations

import asyncio
import socket

from watcherobot.runtime.daemon.pairing.protocol import (
    LinkReuniteAccept,
    encode_udp_message,
    reunite_response_mac,
)
from watcherobot.runtime.daemon.pairing.session import (
    DevicePairingSession,
    DevicePairingState,
)
from watcherobot.runtime.daemon.pairing.udp import (
    PairingUdpChannel,
    PairingUdpInterface,
    PairingUdpService,
)


def test_discovery_receives_after_probe_to_closed_udp_port() -> None:
    async def scenario() -> None:
        received = asyncio.Event()

        async def on_datagram(data: bytes, _address: tuple[str, int]) -> bool:
            if data == b"valid-response":
                received.set()
            return True

        interface = PairingUdpInterface(
            interface_name="loopback",
            local_ip="127.0.0.1",
            netmask="255.0.0.0",
            broadcast_ip="127.255.255.255",
        )
        channel = await PairingUdpChannel.open(interface, 0, on_datagram)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stale:
                stale.bind(("127.0.0.1", 0))
                stale_address = stale.getsockname()
            # Windows reports ICMP port-unreachable on the next receive. Older
            # Proactor loops stop reading this socket permanently after it.
            channel.send_unicast(b"probe", stale_address)
            await asyncio.sleep(0.2)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
                peer.sendto(b"valid-response", ("127.0.0.1", channel.bound_port))
            await asyncio.wait_for(received.wait(), timeout=1.0)
        finally:
            channel.close()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_discovery_reopens_socket_after_connection_reset() -> None:
    async def scenario() -> None:
        interface = PairingUdpInterface("loopback", "127.0.0.1", "255.0.0.0", "127.255.255.255")
        channels: list[PairingUdpChannel] = []

        async def factory(interface, port, handler):
            channel = await PairingUdpChannel.open(interface, port, handler)
            channels.append(channel)
            return channel

        session = DevicePairingSession(daemon_instance_id="a" * 32)
        session.start_reunite_scan(
            request_id="b" * 32, nonce="c" * 32, target_mode="desktop_link",
            websocket_port=8765, binding_secret="d" * 64, now=100,
        )
        service = PairingUdpService(
            session=session, port=0, interface_provider=lambda: (interface,),
            channel_factory=factory, clock=lambda: 100,
        )
        await service.start()
        try:
            # The OS invokes this callback when a stale peer sends ICMP
            # port-unreachable. Old Windows Proactor loops then stop reads.
            channels[0].error_received(ConnectionResetError(10054, "port unreachable"))
            await service.broadcast_once()
            await asyncio.sleep(0)
            await service.broadcast_once()
            assert len(channels) == 2, "discovery reused the socket whose receive loop stopped"
            response = LinkReuniteAccept(
                request_id="b" * 32, daemon_instance_id="a" * 32,
                nonce="c" * 32, target_mode="desktop_link", session_token="e" * 64,
                response_mac=reunite_response_mac(
                    "d" * 64, request_id="b" * 32, nonce="c" * 32,
                    daemon_instance_id="a" * 32, target_mode="desktop_link",
                ),
            )
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
                peer.sendto(encode_udp_message(response), ("127.0.0.1", channels[1].bound_port))
            async def wait_for_accept() -> None:
                while session.state is DevicePairingState.DISCOVERING:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_for_accept(), timeout=1.0)
            assert session.state is DevicePairingState.CONNECTING
        finally:
            await service.stop()

    asyncio.run(scenario())
