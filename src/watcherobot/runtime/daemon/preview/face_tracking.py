"""Lifecycle guard for the face-tracking developer preview."""

from __future__ import annotations

import json
from itertools import count

from ..application.session import ApplicationChannel
from ..connections.registry import (
    ExternalClientRole,
    ExternalConnection,
    ExternalConnectionRegistry,
)


class FaceTrackingPreviewBroker:
    """Remember preview owners and stop motion when a browser disappears."""

    START_TYPE = "ctrl.face_tracking.preview.start"
    STOP_TYPE = "ctrl.face_tracking.preview.stop"

    def __init__(
        self,
        registry: ExternalConnectionRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._owners: set[object] = set()
        self._application_owner = object()
        self._command_sequence = count(1)

    def bind_registry(self, registry: ExternalConnectionRegistry) -> None:
        if self._registry is not None and self._registry is not registry:
            raise RuntimeError("preview broker is already bound")
        self._registry = registry

    async def observe_frame(
        self,
        source: ExternalConnection,
        frame: str | bytes,
    ) -> None:
        if source.role is not ExternalClientRole.DESKTOP:
            return
        self._observe_owner(source.websocket, frame)

    async def observe_application_frame(
        self,
        source: ApplicationChannel,
        frame: str | bytes,
    ) -> None:
        if source is ApplicationChannel.DEVICE:
            self._observe_owner(self._application_owner, frame)

    def _observe_owner(self, owner: object, frame: str | bytes) -> None:
        message_type = self._message_type(frame)
        if message_type == self.START_TYPE:
            self._owners.add(owner)
        elif message_type == self.STOP_TYPE:
            self._owners.clear()

    async def connection_lost(self, connection: ExternalConnection) -> None:
        await self._owner_lost(connection.websocket)

    async def application_channel_lost(
        self,
        channel: ApplicationChannel,
    ) -> None:
        if channel is ApplicationChannel.DEVICE:
            await self._owner_lost(self._application_owner)

    async def _owner_lost(self, owner: object) -> None:
        if owner not in self._owners:
            return
        self._owners.discard(owner)
        if self._owners:
            return
        registry = self._registry
        if registry is None:
            return
        command_id = (
            "daemon-preview-disconnect-"
            f"{next(self._command_sequence)}"
        )
        command = json.dumps(
            {
                "type": self.STOP_TYPE,
                "code": 0,
                "data": {
                    "command_id": command_id,
                    "policy": "hold",
                },
            },
            separators=(",", ":"),
        )
        await registry.send_to_role(ExternalClientRole.DEVICE, command)

    @staticmethod
    def _message_type(frame: str | bytes) -> str | None:
        if not isinstance(frame, str):
            return None
        try:
            message = json.loads(frame)
        except json.JSONDecodeError:
            return None
        if not isinstance(message, dict):
            return None
        message_type = message.get("type")
        data = message.get("data")
        if not isinstance(message_type, str) or not isinstance(data, dict):
            return None
        command_id = data.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            return None
        return message_type
