"""Daemon-initiated UDP transport for watcher-lan-pairing/1.0."""

from __future__ import annotations

import asyncio
import ipaddress
import inspect
import socket
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from typing import Any

from watcherobot.runtime.daemon.pairing.protocol import (
    PairAccept,
    PairBusy,
    PairingProtocolError,
    encode_udp_message,
    parse_udp_message,
)
from watcherobot.runtime.daemon.pairing.session import (
    DevicePairingSession,
    DevicePairingState,
    PairingSessionError,
)


PAIRING_UDP_PORT = 37021
PAIRING_BROADCAST_ADDRESS = "255.255.255.255"
DEFAULT_BROADCAST_INTERVAL_SECONDS = 1.0

StateListener = Callable[
    [Mapping[str, object]],
    Awaitable[None] | None,
]


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def collect_lan_broadcast_addresses(
    *,
    interface_addresses: Mapping[str, Sequence[object]] | None = None,
    interface_stats: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """Return directed broadcasts for active IPv4 LANs plus limited broadcast."""
    if interface_addresses is None or interface_stats is None:
        import psutil

        if interface_addresses is None:
            interface_addresses = psutil.net_if_addrs()
        if interface_stats is None:
            interface_stats = psutil.net_if_stats()

    broadcasts: set[str] = set()
    for interface_name, addresses in interface_addresses.items():
        stats = interface_stats.get(interface_name)
        if stats is not None and not bool(_field(stats, "isup", False)):
            continue
        for address in addresses:
            if _field(address, "family") != socket.AF_INET:
                continue
            ip_value = _field(address, "address")
            netmask = _field(address, "netmask")
            if not isinstance(ip_value, str) or not isinstance(netmask, str):
                continue
            try:
                ip = ipaddress.IPv4Address(ip_value)
                network = ipaddress.IPv4Interface(
                    f"{ip_value}/{netmask}"
                ).network
            except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
                continue
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                continue
            broadcasts.add(str(network.broadcast_address))

    broadcasts.discard(PAIRING_BROADCAST_ADDRESS)
    return (*sorted(broadcasts), PAIRING_BROADCAST_ADDRESS)


class PairingUdpService(asyncio.DatagramProtocol):
    """Broadcast pair requests and feed responses into one pairing session."""

    def __init__(
        self,
        *,
        session: DevicePairingSession,
        host: str = "0.0.0.0",
        port: int = PAIRING_UDP_PORT,
        broadcast_address: str | None = None,
        broadcast_addresses: Sequence[str] | None = None,
        broadcast_interval_seconds: float = DEFAULT_BROADCAST_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        state_listener: StateListener | None = None,
    ) -> None:
        self._session = session
        self._host = host
        self._requested_port = port
        if broadcast_address is not None and broadcast_addresses is not None:
            raise ValueError(
                "broadcast_address and broadcast_addresses are mutually exclusive"
            )
        if broadcast_addresses is not None:
            self._broadcast_addresses = tuple(dict.fromkeys(broadcast_addresses))
        elif broadcast_address is not None:
            self._broadcast_addresses = (broadcast_address,)
        else:
            self._broadcast_addresses = collect_lan_broadcast_addresses()
        self._broadcast_interval_seconds = broadcast_interval_seconds
        self._clock = clock
        self._state_listener = state_listener
        self._transport: Any | None = None
        self._broadcast_task: asyncio.Task[None] | None = None
        self._receive_tasks: set[asyncio.Task[bool]] = set()
        self._bound_port: int | None = None

    @property
    def bound_port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("pairing UDP service is not running")
        return self._bound_port

    async def start(self) -> None:
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: self,
            local_addr=(self._host, self._requested_port),
            allow_broadcast=True,
        )
        if self._transport is None:
            transport.close()
            raise RuntimeError("pairing UDP transport did not initialize")

    async def stop(self) -> None:
        task = self._broadcast_task
        self._broadcast_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        receive_tasks = list(self._receive_tasks)
        for receive_task in receive_tasks:
            receive_task.cancel()
        if receive_tasks:
            await asyncio.gather(*receive_tasks, return_exceptions=True)
        self._receive_tasks.clear()

        transport = self._transport
        self._transport = None
        self._bound_port = None
        if transport is not None:
            transport.close()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport
        socket_info = transport.get_extra_info("sockname")
        if socket_info is not None:
            self._bound_port = int(socket_info[1])
        elif self._requested_port != 0:
            self._bound_port = self._requested_port

    def connection_lost(self, _exc: Exception | None) -> None:
        self._transport = None
        self._bound_port = None

    def datagram_received(
        self,
        data: bytes,
        address: tuple[str, int],
    ) -> None:
        task = asyncio.create_task(
            self.handle_datagram(data, address),
            name="daemon-pairing-udp-receive",
        )
        self._receive_tasks.add(task)
        task.add_done_callback(self._receive_tasks.discard)

    def activate(self) -> None:
        if self._transport is None:
            raise RuntimeError("pairing UDP service is not running")
        if self._session.state not in {
            DevicePairingState.DISCOVERING,
            DevicePairingState.CONNECTING,
            DevicePairingState.RECONNECTING,
        }:
            raise PairingSessionError("invalid_state_transition")
        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(
                self._broadcast_loop(),
                name="daemon-pairing-udp-broadcast",
            )

    async def broadcast_once(self) -> bool:
        request = self._session.current_request
        transport = self._transport
        if (
            transport is None
            or request is None
            or self._session.state is not DevicePairingState.DISCOVERING
        ):
            return False
        datagram = encode_udp_message(request)
        for broadcast_address in self._broadcast_addresses:
            transport.sendto(
                datagram,
                (broadcast_address, PAIRING_UDP_PORT),
            )
        return True

    async def handle_datagram(
        self,
        data: bytes,
        address: tuple[str, int],
    ) -> bool:
        try:
            message = parse_udp_message(data)
            if isinstance(message, PairAccept):
                self._session.accept_device(
                    message,
                    peer_ip=address[0],
                    now=self._clock(),
                )
            elif isinstance(message, PairBusy):
                self._session.reject_busy(message)
            else:
                return False
        except (PairingProtocolError, PairingSessionError):
            return False

        await self._notify_state()
        return True

    async def cancel_pairing(self) -> bool:
        cancel_message = self._session.pending_cancel_message()
        peer_ip = self._session.expected_peer_ip
        cancelled = self._session.cancel()
        if not cancelled:
            return False
        if (
            cancel_message is not None
            and peer_ip is not None
            and self._transport is not None
        ):
            self._transport.sendto(
                encode_udp_message(cancel_message),
                (peer_ip, PAIRING_UDP_PORT),
            )
        await self._notify_state()
        return True

    async def expire_once(self) -> bool:
        expired = self._session.expire(now=self._clock())
        if expired:
            await self._notify_state()
        return expired

    async def _broadcast_loop(self) -> None:
        while self._session.state in {
            DevicePairingState.DISCOVERING,
            DevicePairingState.CONNECTING,
            DevicePairingState.RECONNECTING,
        }:
            if self._session.state is DevicePairingState.DISCOVERING:
                await self.broadcast_once()
            await asyncio.sleep(self._broadcast_interval_seconds)
            await self.expire_once()

    async def _notify_state(self) -> None:
        listener = self._state_listener
        if listener is None:
            return
        result = listener(self._session.snapshot())
        if inspect.isawaitable(result):
            await result
