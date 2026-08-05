"""Per-browser latest-frame delivery with explicit consumer credit."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from websockets.exceptions import ConnectionClosed

from ..connections.registry import (
    ExternalClientRole,
    ExternalConnectionRegistry,
)


PREVIEW_CREDIT_CAPABILITY = "face_tracking.preview.credit.v1"


@dataclass(frozen=True)
class PreviewRelayFrame:
    stream_id: int
    sequence: int
    telemetry: str
    image: bytes
    completed_at: float


@dataclass
class PreviewDeliveryStats:
    offered_frames: int = 0
    sent_frames: int = 0
    acknowledged_frames: int = 0
    pending_overwrites: int = 0
    unexpected_acknowledgements: int = 0
    send_errors: int = 0


@dataclass
class _ConsumerState:
    websocket: Any
    credit_required: bool
    in_flight_sequence: int | None = None
    pending: PreviewRelayFrame | None = None
    pending_overwrites: int = 0
    sender: asyncio.Task[None] | None = None


class LatestPreviewDelivery:
    """Keep at most one in-flight and one replaceable frame per browser."""

    def __init__(
        self,
        registry: ExternalConnectionRegistry,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._registry = registry
        self._clock = clock
        self._consumers: dict[object, _ConsumerState] = {}
        self.stats = PreviewDeliveryStats()

    def offer(self, frame: PreviewRelayFrame) -> int:
        self.stats.offered_frames += 1
        delivered = 0
        for connection in self._registry.connections_for(ExternalClientRole.DESKTOP):
            websocket = connection.websocket
            state = self._consumers.get(websocket)
            if state is None:
                capabilities = connection.metadata.get("capabilities", [])
                state = _ConsumerState(
                    websocket=websocket,
                    credit_required=(
                        isinstance(capabilities, list)
                        and PREVIEW_CREDIT_CAPABILITY in capabilities
                    ),
                )
                self._consumers[websocket] = state
            self._offer_to_consumer(state, frame)
            delivered += 1
        return delivered

    def acknowledge(self, websocket: object, *, sequence: int) -> bool:
        state = self._consumers.get(websocket)
        if state is None or state.in_flight_sequence != sequence:
            self.stats.unexpected_acknowledgements += 1
            return False
        state.in_flight_sequence = None
        self.stats.acknowledged_frames += 1
        if state.sender is None:
            self._send_pending(state)
        return True

    def connection_lost(self, websocket: object) -> None:
        state = self._consumers.pop(websocket, None)
        if state is not None and state.sender is not None:
            state.sender.cancel()

    def discard_pending(self) -> None:
        """Forget unsent frames while preserving at most one in-flight frame."""

        for state in self._consumers.values():
            state.pending = None
            state.pending_overwrites = 0

    async def stop(self) -> None:
        tasks = [
            state.sender
            for state in self._consumers.values()
            if state.sender is not None
        ]
        self._consumers.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def snapshot(self) -> dict[str, object]:
        return {
            **asdict(self.stats),
            "active_consumers": len(self._consumers),
            "credit_consumers": sum(
                1 for state in self._consumers.values() if state.credit_required
            ),
        }

    def _offer_to_consumer(
        self,
        state: _ConsumerState,
        frame: PreviewRelayFrame,
    ) -> None:
        if state.in_flight_sequence is None and state.sender is None:
            self._start_send(state, frame, skipped_frames=0)
            return
        if state.pending is not None:
            state.pending_overwrites += 1
            self.stats.pending_overwrites += 1
        state.pending = frame

    def _send_pending(self, state: _ConsumerState) -> None:
        frame = state.pending
        if frame is None or state.sender is not None:
            return
        skipped_frames = state.pending_overwrites
        state.pending = None
        state.pending_overwrites = 0
        self._start_send(state, frame, skipped_frames=skipped_frames)

    def _start_send(
        self,
        state: _ConsumerState,
        frame: PreviewRelayFrame,
        *,
        skipped_frames: int,
    ) -> None:
        state.in_flight_sequence = frame.sequence
        state.sender = asyncio.create_task(
            self._send(state, frame, skipped_frames),
            name=f"preview-delivery-{frame.sequence}",
        )

    async def _send(
        self,
        state: _ConsumerState,
        frame: PreviewRelayFrame,
        skipped_frames: int,
    ) -> None:
        failed = False
        try:
            telemetry = self._telemetry_for_delivery(frame, skipped_frames)
            await state.websocket.send(telemetry)
            await state.websocket.send(frame.image)
            self.stats.sent_frames += 1
        except (
            ConnectionClosed,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            failed = True
            self.stats.send_errors += 1
        finally:
            state.sender = None
            if self._consumers.get(state.websocket) is not state:
                state.pending = None
                state.pending_overwrites = 0
                return
            if failed:
                state.in_flight_sequence = None
                state.pending = None
                state.pending_overwrites = 0
                self._consumers.pop(state.websocket, None)
                return
            if not state.credit_required:
                state.in_flight_sequence = None
            if state.in_flight_sequence is None:
                self._send_pending(state)

    def _telemetry_for_delivery(
        self,
        frame: PreviewRelayFrame,
        skipped_frames: int,
    ) -> str:
        payload = json.loads(frame.telemetry)
        queue_ms = round(max(0.0, self._clock() - frame.completed_at) * 1000, 1)
        payload["relay"] = [
            int(round(frame.completed_at * 1000)),
            queue_ms,
            skipped_frames,
        ]
        return json.dumps(payload, separators=(",", ":"))
