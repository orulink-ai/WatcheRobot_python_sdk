"""Service-level recovery regression using real UDP sockets."""

from __future__ import annotations

import asyncio
import json
import socket

from watcherobot.runtime.daemon.pairing.protocol import (
    LAN_PAIRING_PROTOCOL,
    LinkReuniteAccept,
    LinkReuniteAvailable,
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


def test_idle_listener_receives_availability_after_connection_reset() -> None:
    async def scenario() -> None:
        interface = PairingUdpInterface("loopback", "127.0.0.1", "255.0.0.0", "127.255.255.255")
        channels: list[PairingUdpChannel] = []
        replacement_ready = asyncio.Event()
        availability_received = asyncio.Event()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            fixed_port = port_probe.getsockname()[1]
        open_attempts = 0
        interface_reads = 0

        def interfaces():
            nonlocal interface_reads
            interface_reads += 1
            if interface_reads == 2:
                raise OSError("simulated temporary interface enumeration failure")
            if interface_reads == 3:
                return ()
            return (interface,)
        message = LinkReuniteAvailable("a" * 32, "desktop_link")
        datagram = json.dumps({
            "type": "link.reunite.available",
            "protocol": LAN_PAIRING_PROTOCOL,
            "version": "1.1",
            "daemon_instance_id": message.daemon_instance_id,
            "target_mode": message.target_mode,
        }).encode()

        async def factory(interface, port, handler):
            nonlocal open_attempts
            open_attempts += 1
            if open_attempts == 2:
                raise OSError("simulated port still closing")
            channel = await PairingUdpChannel.open(interface, port, handler)
            channels.append(channel)
            if len(channels) == 2:
                replacement_ready.set()
            return channel

        async def on_available(hint, peer_ip):
            assert hint == message
            assert peer_ip == "127.0.0.1"
            availability_received.set()
            return True

        session = DevicePairingSession(daemon_instance_id="a" * 32)
        service = PairingUdpService(
            session=session, port=fixed_port, interface_provider=interfaces,
            channel_factory=factory, broadcast_interval_seconds=0.02,
            idle_listener=lambda: asyncio.sleep(0), availability_listener=on_available,
        )
        await service.start()
        try:
            channels[0].error_received(ConnectionResetError(10054, "port unreachable"))
            await asyncio.wait_for(replacement_ready.wait(), timeout=8.0)
            assert open_attempts >= 3
            assert service.bound_port == fixed_port
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
                peer.sendto(datagram, ("127.0.0.1", fixed_port))
            await asyncio.wait_for(availability_received.wait(), timeout=1.0)
            assert session.state is DevicePairingState.IDLE
        finally:
            await service.stop()

    asyncio.run(scenario())
