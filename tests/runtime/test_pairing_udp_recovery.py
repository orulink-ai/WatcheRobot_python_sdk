"""Service-level recovery regression using real UDP sockets."""

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


def test_discovery_reopens_socket_after_connection_reset() -> None:
    async def scenario() -> None:
        interface = PairingUdpInterface("loopback", "127.0.0.1", "255.0.0.0", "127.255.255.255")
        channels: list[PairingUdpChannel] = []
        replacement_ready = asyncio.Event()

        async def factory(interface, port, handler):
            channel = await PairingUdpChannel.open(interface, port, handler)
            channels.append(channel)
            if len(channels) == 2:
                replacement_ready.set()
            return channel

        session = DevicePairingSession(daemon_instance_id="a" * 32)
        session.start_reunite_scan(
            request_id="b" * 32, nonce="c" * 32, target_mode="desktop_link",
            websocket_port=8765, binding_secret="d" * 64, now=100,
        )
        service = PairingUdpService(
            session=session, port=0, interface_provider=lambda: (interface,),
            channel_factory=factory, clock=lambda: 100, broadcast_interval_seconds=0.02,
        )
        await service.start()
        try:
            # The OS invokes this callback when a stale peer sends ICMP
            # port-unreachable. Old Windows Proactor loops then stop reads.
            channels[0].error_received(ConnectionResetError(10054, "port unreachable"))
            service.activate()
            await asyncio.wait_for(replacement_ready.wait(), timeout=2.0)
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
