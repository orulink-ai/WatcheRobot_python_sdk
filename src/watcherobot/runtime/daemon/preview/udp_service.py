"""Authenticated UDP receiver that relays newest preview frames locally."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..connections.registry import ExternalClientRole, ExternalConnectionRegistry
from ..pairing.session import DevicePairingSession
from .udp_feedback import encode_preview_ack
from .udp_protocol import (
    CompletedPreviewFrame,
    FaceTrackingUdpProtocolError,
    FaceTrackingUdpReassembler,
    parse_preview_bundle,
)


@dataclass
class FaceTrackingUdpPreviewStats:
    datagrams_received: int = 0
    wrong_source_datagrams: int = 0
    inactive_session_datagrams: int = 0
    invalid_datagrams: int = 0
    completed_frames: int = 0
    publish_overwrites: int = 0
    published_frames: int = 0
    feedback_sent: int = 0
    feedback_errors: int = 0


class _DatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: "FaceTrackingUdpPreviewService") -> None:
        self._service = service

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._service.handle_datagram(data, addr)


class FaceTrackingUdpPreviewService:
    """Own one UDP socket and one bounded latest-frame publishing slot."""

    def __init__(
        self,
        *,
        session: DevicePairingSession,
        registry: ExternalConnectionRegistry,
        publisher: Callable[[str | bytes], Awaitable[int]] | None = None,
        host: str = "0.0.0.0",
        port: int = 37022,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session = session
        self._registry = registry
        self._publisher_callback = publisher
        self._host = host
        self._port = port
        self._clock = clock
        self._transport: asyncio.DatagramTransport | None = None
        self._publisher: asyncio.Task[None] | None = None
        self._publish_event = asyncio.Event()
        self._pending: CompletedPreviewFrame | None = None
        self._pending_completed_at = 0.0
        self._reassembler: FaceTrackingUdpReassembler | None = None
        self._active_key: bytes | None = None
        self.stats = FaceTrackingUdpPreviewStats()

    @property
    def bound_port(self) -> int:
        if self._transport is None:
            return 0
        address: Any = self._transport.get_extra_info("sockname")
        return int(address[1]) if address else 0

    def snapshot(self) -> dict[str, object]:
        reassembly = (
            asdict(self._reassembler.stats) if self._reassembler is not None else None
        )
        return {
            "mode": "udp_latest_frame",
            "port": self.bound_port,
            "service": asdict(self.stats),
            "reassembly": reassembly,
        }

    async def start(self) -> None:
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DatagramProtocol(self),
            local_addr=(self._host, self._port),
        )
        self._transport = transport
        self._publisher = asyncio.create_task(
            self._publish_loop(), name="face-tracking-udp-publisher"
        )

    async def stop(self) -> None:
        transport = self._transport
        self._transport = None
        if transport is not None:
            transport.close()
        publisher = self._publisher
        self._publisher = None
        if publisher is not None:
            publisher.cancel()
            try:
                await publisher
            except asyncio.CancelledError:
                pass
        self._reset_session()

    def handle_datagram(self, data: bytes, address: tuple[str, int]) -> None:
        self.stats.datagrams_received += 1
        credentials = self._session.preview_transport_credentials()
        if credentials is None:
            self.stats.inactive_session_datagrams += 1
            self._reset_session()
            return
        expected_ip, session_key = credentials
        if address[0] != expected_ip:
            self.stats.wrong_source_datagrams += 1
            return
        if session_key != self._active_key:
            self._active_key = session_key
            self._reassembler = FaceTrackingUdpReassembler(
                session_key=session_key, clock=self._clock
            )
        assert self._reassembler is not None
        malformed_before = self._reassembler.stats.malformed_datagrams
        frame = self._reassembler.push(data)
        self.stats.invalid_datagrams += (
            self._reassembler.stats.malformed_datagrams - malformed_before
        )
        if frame is None:
            return
        self.stats.completed_frames += 1
        self._send_feedback(frame, address, session_key)
        if self._pending is not None:
            self.stats.publish_overwrites += 1
        self._pending = frame
        self._pending_completed_at = self._clock()
        self._publish_event.set()

    def _send_feedback(
        self,
        frame: CompletedPreviewFrame,
        address: tuple[str, int],
        session_key: bytes,
    ) -> None:
        transport = self._transport
        if transport is None:
            self.stats.feedback_errors += 1
            return
        try:
            transport.sendto(
                encode_preview_ack(
                    session_key=session_key,
                    stream_id=frame.stream_id,
                    sequence=frame.sequence,
                ),
                address,
            )
        except OSError:
            self.stats.feedback_errors += 1
            return
        self.stats.feedback_sent += 1

    async def _publish_loop(self) -> None:
        while True:
            await self._publish_event.wait()
            self._publish_event.clear()
            frame = self._pending
            completed_at = self._pending_completed_at
            self._pending = None
            if frame is None:
                continue
            try:
                telemetry, image = parse_preview_bundle(frame.bundle)
                payload = json.loads(telemetry)
                if not isinstance(payload, dict):
                    raise FaceTrackingUdpProtocolError(
                        "preview telemetry is not an object"
                    )
                published_at = self._clock()
                payload["relay"] = [
                    int(round(completed_at * 1000)),
                    round(max(0.0, published_at - completed_at) * 1000, 1),
                ]
                telemetry = json.dumps(payload, separators=(",", ":"))
            except (FaceTrackingUdpProtocolError, json.JSONDecodeError):
                self.stats.invalid_datagrams += 1
                continue
            await self._publish(telemetry)
            await self._publish(image)
            self.stats.published_frames += 1

    async def _publish(self, frame: str | bytes) -> int:
        callback = self._publisher_callback
        if callback is not None:
            return await callback(frame)
        return await self._registry.send_to_role(ExternalClientRole.DESKTOP, frame)

    def _reset_session(self) -> None:
        self._active_key = None
        self._reassembler = None
        self._pending = None
        self._pending_completed_at = 0.0
        self._publish_event.clear()
