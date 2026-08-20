"""Connection-only hardware state without device hello metadata."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count


@dataclass(frozen=True)
class DeviceConnectionState:
    connection_id: int
    online: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "online": self.online,
        }


class DeviceConnectionStateRegistry:
    """Track current hardware sockets without inferring persistent identity."""

    def __init__(self) -> None:
        self._states: dict[object, DeviceConnectionState] = {}
        self._connection_ids = count(1)

    def connect(self, websocket: object) -> DeviceConnectionState:
        state = DeviceConnectionState(connection_id=next(self._connection_ids))
        self._states[websocket] = state
        return state

    def disconnect(self, websocket: object) -> None:
        self._states.pop(websocket, None)

    def snapshot(self) -> list[dict[str, object]]:
        return [
            state.to_dict()
            for state in sorted(
                self._states.values(),
                key=lambda item: item.connection_id,
            )
        ]
