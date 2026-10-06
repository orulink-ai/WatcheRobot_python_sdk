"""Account for packets between the sender and the device's receive watermark."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AudioInFlightWindow:
    """One stream, accessed under its transport's audio condition lock."""

    reserved: int = 0
    acknowledged: int = 0
    has_watermark: bool = False

    def reserve(self) -> None:
        # Reserve before awaiting channel.send(): a fast reply can arrive there.
        self.reserved += 1

    def available(self, data: dict[str, Any], reported_credits: int) -> int | None:
        if "expected_rx_seq" not in data:
            # Legacy firmware has no cumulative acknowledgement. Once seen,
            # never downgrade this stream back to unbounded snapshot credits.
            return None if self.has_watermark else reported_credits
        received = data["expected_rx_seq"]
        if (
            not isinstance(received, int)
            or isinstance(received, bool)
            or not self.acknowledged <= received <= self.reserved
        ):
            return None
        self.acknowledged = received
        self.has_watermark = True
        in_flight = self.reserved - self.acknowledged
        return max(0, reported_credits - in_flight)
