"""Daemon-initiated multi-interface UDP transport for watcher-lan-pairing/1.0."""

from __future__ import annotations

import asyncio
import ipaddress
import inspect
import logging
import socket
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast

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

_LOGGER = logging.getLogger(__name__)

StateListener = Callable[
    [Mapping[str, object]],
    Awaitable[None] | None,
]


@dataclass(frozen=True)
class PairingUdpInterface:
    """One active IPv4 LAN path used by the pairing service."""

    interface_name: str
    local_ip: str
    netmask: str
    broadcast_ip: str


DatagramHandler = Callable[
    [bytes, tuple[str, int]],
    Coroutine[Any, Any, bool],
]


class PairingUdpChannelContract(Protocol):
    """Transport boundary used to test channel reconciliation without real NICs."""

    interface: PairingUdpInterface

    @property
    def bound_port(self) -> int: ...

    def send_broadcast(self, data: bytes) -> None: ...

    def send_unicast(self, data: bytes, address: tuple[str, int]) -> None: ...

    def close(self) -> None: ...


ChannelFactory = Callable[
    [PairingUdpInterface, int, DatagramHandler],
    Awaitable[PairingUdpChannelContract],
]
InterfaceProvider = Callable[[], Sequence[PairingUdpInterface]]
EventLogger = Callable[[str], object]


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def collect_lan_interfaces(
    *,
    interface_addresses: Mapping[str, Sequence[object]] | None = None,
    interface_stats: Mapping[str, object] | None = None,
) -> tuple[PairingUdpInterface, ...]:
    """Return active broadcast-capable IPv4 interfaces without cross-NIC deduplication."""
    if interface_addresses is None or interface_stats is None:
        import psutil

        if interface_addresses is None:
            interface_addresses = psutil.net_if_addrs()
        if interface_stats is None:
            interface_stats = psutil.net_if_stats()

    interfaces: set[PairingUdpInterface] = set()
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
                ipv4_interface = ipaddress.IPv4Interface(
                    f"{ip_value}/{netmask}"
                )
            except (ipaddress.AddressValueError, ipaddress.NetmaskValueError):
                continue
            if (
                ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
            ):
                continue
            network = ipv4_interface.network
            if network.prefixlen >= 31:
                continue
            broadcast_ip = str(network.broadcast_address)
            if broadcast_ip == PAIRING_BROADCAST_ADDRESS:
                continue
            interfaces.add(
                PairingUdpInterface(
                    interface_name=interface_name,
                    local_ip=ip_value,
                    netmask=netmask,
                    broadcast_ip=broadcast_ip,
                )
            )

    return tuple(
        sorted(
            interfaces,
            key=lambda item: (
                item.interface_name.casefold(),
                ipaddress.IPv4Address(item.local_ip),
                item.netmask,
            ),
        )
    )


class PairingUdpChannel(asyncio.DatagramProtocol):
    """One UDP socket bound to one local interface IPv4 address."""

    def __init__(
        self,
        *,
        interface: PairingUdpInterface,
        datagram_handler: DatagramHandler,
    ) -> None:
        self.interface = interface
        self._datagram_handler = datagram_handler
        self._transport: asyncio.DatagramTransport | None = None
        self._receive_tasks: set[asyncio.Task[bool]] = set()

    @classmethod
    async def open(
        cls,
        interface: PairingUdpInterface,
        port: int,
        datagram_handler: DatagramHandler,
    ) -> PairingUdpChannel:
        protocol = cls(
            interface=interface,
            datagram_handler=datagram_handler,
        )
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            local_addr=(interface.local_ip, port),
            family=socket.AF_INET,
            allow_broadcast=True,
        )
        if protocol._transport is None:
            transport.close()
            raise RuntimeError(
                "pairing UDP channel transport did not initialize"
            )
        return protocol

    @property
    def bound_port(self) -> int:
        transport = self._transport
        if transport is None:
            raise RuntimeError("pairing UDP channel is not running")
        socket_info = transport.get_extra_info("sockname")
        if socket_info is None:
            raise RuntimeError("pairing UDP channel has no socket address")
        return int(socket_info[1])

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = cast(asyncio.DatagramTransport, transport)

    def connection_lost(self, _exc: Exception | None) -> None:
        self._transport = None

    def error_received(self, exc: Exception) -> None:
        _LOGGER.warning(
            "Pairing UDP channel error interface=%s local=%s error=%s",
            self.interface.interface_name,
            self.interface.local_ip,
            exc,
        )

    def datagram_received(
        self,
        data: bytes,
        address: tuple[str, int],
    ) -> None:
        task: asyncio.Task[bool] = asyncio.create_task(
            self._datagram_handler(data, address),
            name=(
                "daemon-pairing-udp-receive-"
                f"{self.interface.interface_name}-{self.interface.local_ip}"
            ),
        )
        self._receive_tasks.add(task)
        task.add_done_callback(self._receive_tasks.discard)

    def send_broadcast(self, data: bytes) -> None:
        transport = self._transport
        if transport is None:
            raise RuntimeError("pairing UDP channel is not running")
        transport.sendto(
            data,
            (self.interface.broadcast_ip, PAIRING_UDP_PORT),
        )

    def send_unicast(
        self,
        data: bytes,
        address: tuple[str, int],
    ) -> None:
        transport = self._transport
        if transport is None:
            raise RuntimeError("pairing UDP channel is not running")
        transport.sendto(data, address)

    def close(self) -> None:
        for task in tuple(self._receive_tasks):
            task.cancel()
        self._receive_tasks.clear()
        transport = self._transport
        self._transport = None
        if transport is not None:
            transport.close()


async def _open_pairing_udp_channel(
    interface: PairingUdpInterface,
    port: int,
    datagram_handler: DatagramHandler,
) -> PairingUdpChannelContract:
    return await PairingUdpChannel.open(
        interface,
        port,
        datagram_handler,
    )


class PairingUdpService:
    """Own one global pairing session and a dynamic channel per local IPv4."""

    def __init__(
        self,
        *,
        session: DevicePairingSession,
        port: int = PAIRING_UDP_PORT,
        broadcast_interval_seconds: float = DEFAULT_BROADCAST_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        state_listener: StateListener | None = None,
        interface_provider: InterfaceProvider = collect_lan_interfaces,
        channel_factory: ChannelFactory = _open_pairing_udp_channel,
        event_logger: EventLogger | None = None,
    ) -> None:
        self._session = session
        self._requested_port = port
        self._broadcast_interval_seconds = broadcast_interval_seconds
        self._clock = clock
        self._state_listener = state_listener
        self._interface_provider = interface_provider
        self._channel_factory = channel_factory
        self._event_logger = event_logger
        self._channels: dict[
            PairingUdpInterface,
            PairingUdpChannelContract,
        ] = {}
        self._channel_errors: dict[PairingUdpInterface, str] = {}
        self._broadcast_task: asyncio.Task[None] | None = None
        self._selected_interface: PairingUdpInterface | None = None
        self._last_receive_context: dict[str, str] | None = None
        self._refresh_lock = asyncio.Lock()
        self._running = False

    @property
    def bound_port(self) -> int:
        if not self._running:
            raise RuntimeError("pairing UDP service is not running")
        first_channel = next(iter(self._channels.values()), None)
        if first_channel is not None:
            return first_channel.bound_port
        return self._requested_port

    @property
    def active_interfaces(self) -> tuple[PairingUdpInterface, ...]:
        return tuple(self._channels)

    @property
    def channel_errors(self) -> Mapping[PairingUdpInterface, str]:
        return dict(self._channel_errors)

    @property
    def last_receive_context(self) -> Mapping[str, str] | None:
        if self._last_receive_context is None:
            return None
        return dict(self._last_receive_context)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            await self.refresh_channels(require_channel=True)
        except Exception:
            self._running = False
            self._close_all_channels()
            raise

    async def stop(self) -> None:
        task = self._broadcast_task
        self._broadcast_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._close_all_channels()
        self._channel_errors.clear()
        self._selected_interface = None
        self._running = False

    async def refresh_channels(
        self,
        *,
        require_channel: bool = False,
    ) -> tuple[PairingUdpInterface, ...]:
        """Reconcile live sockets with the current network interface snapshot."""
        if not self._running:
            raise RuntimeError("pairing UDP service is not running")
        async with self._refresh_lock:
            desired = tuple(dict.fromkeys(self._interface_provider()))
            desired_set = set(desired)

            for interface in desired:
                if interface in self._channels:
                    self._channel_errors.pop(interface, None)
                    continue
                handler = self._handler_for(interface)
                try:
                    channel = await self._channel_factory(
                        interface,
                        self._requested_port,
                        handler,
                    )
                except (OSError, RuntimeError) as exc:
                    self._channel_errors[interface] = str(exc)
                    self._emit_event(
                        "Unable to open pairing UDP channel "
                        f"(interface={interface.interface_name}, "
                        f"local={interface.local_ip}, "
                        f"broadcast={interface.broadcast_ip}, "
                        f"error={exc})",
                        warning=True,
                    )
                    continue
                self._channels[interface] = channel
                self._channel_errors.pop(interface, None)
                self._emit_event(
                    "Pairing UDP channel ready "
                    f"(interface={interface.interface_name}, "
                    f"local={interface.local_ip}, "
                    f"broadcast={interface.broadcast_ip}, "
                    f"port={channel.bound_port})"
                )

            stale = [
                interface
                for interface in self._channels
                if interface not in desired_set
            ]
            for interface in stale:
                channel = self._channels.pop(interface)
                channel.close()
                if self._selected_interface == interface:
                    self._selected_interface = None
                self._emit_event(
                    "Pairing UDP channel removed "
                    f"(interface={interface.interface_name}, "
                    f"local={interface.local_ip})"
                )

            for interface in tuple(self._channel_errors):
                if interface not in desired_set:
                    self._channel_errors.pop(interface, None)

            if require_channel and desired and not self._channels:
                raise RuntimeError(
                    "no pairing UDP interface could be bound"
                )

            return self.active_interfaces

    def activate(self) -> None:
        if not self._running:
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
        if (
            not self._running
            or request is None
            or self._session.state is not DevicePairingState.DISCOVERING
        ):
            return False

        await self.refresh_channels()
        datagram = encode_udp_message(request)
        sent = False
        for interface, channel in tuple(self._channels.items()):
            try:
                channel.send_broadcast(datagram)
            except (OSError, RuntimeError) as exc:
                self._channel_errors[interface] = str(exc)
                channel.close()
                self._channels.pop(interface, None)
                if self._selected_interface == interface:
                    self._selected_interface = None
                self._emit_event(
                    "Pairing UDP broadcast failed "
                    f"(interface={interface.interface_name}, "
                    f"local={interface.local_ip}, "
                    f"broadcast={interface.broadcast_ip}, "
                    f"error={exc})",
                    warning=True,
                )
                continue
            sent = True
        return sent

    async def handle_datagram(
        self,
        data: bytes,
        address: tuple[str, int],
        *,
        interface: PairingUdpInterface | None = None,
    ) -> bool:
        try:
            message = parse_udp_message(data)
            if isinstance(message, PairAccept):
                self._session.accept_device(
                    message,
                    peer_ip=address[0],
                    now=self._clock(),
                )
                self._selected_interface = interface
                self._record_receive_context(address[0], interface)
            elif isinstance(message, PairBusy):
                self._session.reject_busy(message)
                self._record_receive_context(address[0], interface)
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
        if cancel_message is not None and peer_ip is not None:
            channel = self._channel_for_peer(peer_ip)
            if channel is not None:
                try:
                    channel.send_unicast(
                        encode_udp_message(cancel_message),
                        (peer_ip, PAIRING_UDP_PORT),
                    )
                except (OSError, RuntimeError) as exc:
                    self._emit_event(
                        "Pairing UDP cancel failed "
                        f"(peer={peer_ip}, error={exc})",
                        warning=True,
                    )
        self._selected_interface = None
        await self._notify_state()
        return True

    async def expire_once(self) -> bool:
        expired = self._session.expire(now=self._clock())
        if expired:
            self._selected_interface = None
            await self._notify_state()
        return expired

    def _handler_for(
        self,
        interface: PairingUdpInterface,
    ) -> DatagramHandler:
        async def handler(
            data: bytes,
            address: tuple[str, int],
        ) -> bool:
            return await self.handle_datagram(
                data,
                address,
                interface=interface,
            )

        return handler

    def _record_receive_context(
        self,
        peer_ip: str,
        interface: PairingUdpInterface | None,
    ) -> None:
        if interface is None:
            self._last_receive_context = None
            return
        self._last_receive_context = {
            "peer_ip": peer_ip,
            "local_ip": interface.local_ip,
            "interface_name": interface.interface_name,
        }
        self._emit_event(
            "Pairing UDP response accepted "
            f"(peer={peer_ip}, local={interface.local_ip}, "
            f"interface={interface.interface_name})"
        )

    def _emit_event(
        self,
        message: str,
        *,
        warning: bool = False,
    ) -> None:
        if warning:
            _LOGGER.warning(message)
        else:
            _LOGGER.info(message)
        logger = self._event_logger
        if logger is not None:
            logger(message)

    def _channel_for_peer(
        self,
        peer_ip: str,
    ) -> PairingUdpChannelContract | None:
        selected = self._selected_interface
        if selected is not None:
            selected_channel = self._channels.get(selected)
            if selected_channel is not None:
                return selected_channel
        try:
            peer = ipaddress.IPv4Address(peer_ip)
        except ipaddress.AddressValueError:
            return None
        for interface, channel in self._channels.items():
            network = ipaddress.IPv4Interface(
                f"{interface.local_ip}/{interface.netmask}"
            ).network
            if peer in network:
                return channel
        return None

    def _close_all_channels(self) -> None:
        for channel in self._channels.values():
            channel.close()
        self._channels.clear()

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
