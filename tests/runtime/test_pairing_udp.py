from __future__ import annotations

import asyncio
import json

from watcherobot.runtime.daemon.pairing.protocol import PairAccept, PairBusy, encode_udp_message
from watcherobot.runtime.daemon.pairing.session import DevicePairingSession, DevicePairingState
from watcherobot.runtime.daemon.pairing.udp import PairingUdpService, collect_lan_broadcast_addresses


DAEMON_ID = "f730f29e670c49f7a3320c4314eb9805"
REQUEST_ID = "21a9dbf05ea3443480e62076f79a3b12"
SESSION_TOKEN = (
    "f84a1e16ce6f35f14d167f227a93ea93"
    "d1a9c4d9eb5517112030f2839d57ae4b"
)


class FakeDatagramTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        self.sent.append((data, address))

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, name: str):
        if name == "sockname":
            return ("0.0.0.0", 37021)
        return None


def make_session() -> DevicePairingSession:
    return DevicePairingSession(
        daemon_instance_id=DAEMON_ID,
        request_id_factory=lambda: REQUEST_ID,
    )


def test_collect_lan_broadcast_addresses_covers_each_active_lan() -> None:
    addresses = collect_lan_broadcast_addresses(
        interface_addresses={
            "Ethernet": [
                {
                    "family": 2,
                    "address": "192.168.31.99",
                    "netmask": "255.255.255.0",
                }
            ],
            "WLAN": [
                {
                    "family": 2,
                    "address": "192.168.1.119",
                    "netmask": "255.255.255.0",
                }
            ],
            "Loopback": [
                {
                    "family": 2,
                    "address": "127.0.0.1",
                    "netmask": "255.0.0.0",
                }
            ],
        },
        interface_stats={
            "Ethernet": {"isup": True},
            "WLAN": {"isup": True},
            "Loopback": {"isup": True},
        },
    )

    assert addresses == (
        "192.168.1.255",
        "192.168.31.255",
        "255.255.255.255",
    )


def test_collect_lan_broadcast_addresses_skips_inactive_lans() -> None:
    addresses = collect_lan_broadcast_addresses(
        interface_addresses={
            "Disconnected": [
                {
                    "family": 2,
                    "address": "192.168.50.10",
                    "netmask": "255.255.255.0",
                }
            ],
            "WSL": [
                {
                    "family": 2,
                    "address": "172.22.48.1",
                    "netmask": "255.255.240.0",
                }
            ],
        },
        interface_stats={
            "Disconnected": {"isup": False},
            "WSL": {"isup": True},
        },
    )

    assert addresses == ("172.22.63.255", "255.255.255.255")


def test_udp_service_broadcasts_daemon_owned_pair_request() -> None:
    async def scenario() -> None:
        session = make_session()
        request = session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=10.0,
        )
        states: list[str] = []
        service = PairingUdpService(
            session=session,
            clock=lambda: 10.0,
            broadcast_addresses=("192.168.1.255", "192.168.31.255"),
            state_listener=lambda snapshot: states.append(str(snapshot["state"])),
        )
        transport = FakeDatagramTransport()
        service.connection_made(transport)

        assert await service.broadcast_once() is True
        assert [address for _, address in transport.sent] == [
            ("192.168.1.255", 37021),
            ("192.168.31.255", 37021),
        ]
        datagram = transport.sent[0][0]
        assert json.loads(datagram) == {
            "type": "pair.request",
            "protocol": "watcher-lan-pairing",
            "version": "1.0",
            "request_id": request.request_id,
            "daemon_instance_id": DAEMON_ID,
            "pairing_code": "123456",
            "target_mode": "desktop_link",
            "websocket_port": 8765,
        }
        assert states == []

    asyncio.run(scenario())

def test_udp_service_accepts_first_matching_device_and_stops_broadcasting() -> None:
    async def scenario() -> None:
        session = make_session()
        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=10.0,
        )
        states: list[str] = []
        service = PairingUdpService(
            session=session,
            clock=lambda: 12.0,
            state_listener=lambda snapshot: states.append(str(snapshot["state"])),
        )
        transport = FakeDatagramTransport()
        service.connection_made(transport)
        response = PairAccept(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            target_mode="desktop_link",
            session_token=SESSION_TOKEN,
        )

        assert await service.handle_datagram(
            encode_udp_message(response),
            ("192.168.3.25", 37021),
        )
        assert session.state is DevicePairingState.CONNECTING
        assert session.expected_peer_ip == "192.168.3.25"
        assert states == ["connecting"]
        assert await service.broadcast_once() is False

        assert not await service.handle_datagram(
            encode_udp_message(response),
            ("192.168.3.26", 37021),
        )
        assert session.expected_peer_ip == "192.168.3.25"

    asyncio.run(scenario())


def test_udp_service_handles_busy_invalid_and_cancel_without_leaking_slot() -> None:
    async def scenario() -> None:
        session = make_session()
        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=10.0,
        )
        states: list[dict[str, object]] = []
        service = PairingUdpService(
            session=session,
            clock=lambda: 12.0,
            state_listener=lambda snapshot: states.append(dict(snapshot)),
        )
        transport = FakeDatagramTransport()
        service.connection_made(transport)

        assert not await service.handle_datagram(
            b'{"type":"SDK_DISCOVER"}',
            ("192.168.3.99", 37021),
        )
        assert session.state is DevicePairingState.DISCOVERING

        busy = PairBusy(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            reason="device_session_active",
        )
        assert await service.handle_datagram(
            encode_udp_message(busy),
            ("192.168.3.25", 37021),
        )
        assert session.state is DevicePairingState.IDLE
        assert states[-1]["last_error"] == "device_busy"

        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=20.0,
        )
        accept = PairAccept(
            request_id=REQUEST_ID,
            daemon_instance_id=DAEMON_ID,
            target_mode="desktop_link",
            session_token=SESSION_TOKEN,
        )
        await service.handle_datagram(
            encode_udp_message(accept),
            ("192.168.3.25", 37021),
        )
        assert await service.cancel_pairing() is True
        assert session.state is DevicePairingState.IDLE
        cancel_payload, cancel_address = transport.sent[-1]
        assert cancel_address == ("192.168.3.25", 37021)
        assert json.loads(cancel_payload)["type"] == "pair.cancel"
        assert json.loads(cancel_payload)["session_token"] == SESSION_TOKEN

    asyncio.run(scenario())


def test_udp_service_expires_discovery_and_notifies_state() -> None:
    async def scenario() -> None:
        now = 10.0
        session = make_session()
        session.start_pairing(
            pairing_code="123456",
            target_mode="desktop_link",
            websocket_port=8765,
            now=now,
        )
        states: list[dict[str, object]] = []
        service = PairingUdpService(
            session=session,
            clock=lambda: 20.0,
            state_listener=lambda snapshot: states.append(dict(snapshot)),
        )
        service.connection_made(FakeDatagramTransport())

        assert await service.expire_once() is True
        assert session.state is DevicePairingState.IDLE
        assert states[-1]["last_error"] == "pairing_not_found"

    asyncio.run(scenario())
