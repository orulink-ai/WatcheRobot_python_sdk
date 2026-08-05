"""Content-agnostic routing for external desktop and device frames."""

from __future__ import annotations

from ..application.bridge import (
    ApplicationBridge,
    ChannelNotConnectedError,
)
from ..application.session import (
    ApplicationChannel,
    ApplicationSessionRegistry,
)
from ..connections.registry import (
    ExternalClientRole,
    ExternalConnection,
    ExternalConnectionRegistry,
)


class RawFrameRouter:
    """Choose a destination only from the source connection role."""

    def __init__(
        self,
        registry: ExternalConnectionRegistry,
        *,
        application_registry: ApplicationSessionRegistry | None = None,
        application_bridge: ApplicationBridge | None = None,
    ) -> None:
        self._registry = registry
        self._application_registry = application_registry
        self._application_bridge = application_bridge

    @property
    def application_active(self) -> bool:
        return (
            self._application_registry is not None
            and self._application_registry.active_run is not None
        )

    async def route_external(
        self,
        source: ExternalConnection,
        frame: str | bytes,
    ) -> int:
        if self.application_active:
            channel = _application_channel_for_external(source.role)
            if channel is None or self._application_bridge is None:
                return 0
            try:
                await self._application_bridge.send_to_application(
                    channel,
                    frame,
                )
            except ChannelNotConnectedError:
                return 0
            return 1

        if source.role is ExternalClientRole.DESKTOP:
            return await self._registry.send_to_role(
                ExternalClientRole.DEVICE,
                frame,
            )
        if source.role in (ExternalClientRole.DEVICE, ExternalClientRole.MEDIA):
            return await self._registry.send_to_role(
                ExternalClientRole.DESKTOP,
                frame,
            )
        return 0

    async def route_application(
        self,
        source: ApplicationChannel,
        frame: str | bytes,
    ) -> int:
        if not self.application_active:
            return 0
        role = (
            ExternalClientRole.DESKTOP
            if source is ApplicationChannel.DESKTOP
            else ExternalClientRole.DEVICE
        )
        return await self._registry.send_to_role(role, frame)


def _application_channel_for_external(
    role: ExternalClientRole,
) -> ApplicationChannel | None:
    if role is ExternalClientRole.DESKTOP:
        return ApplicationChannel.DESKTOP
    if role in (ExternalClientRole.DEVICE, ExternalClientRole.MEDIA):
        return ApplicationChannel.DEVICE
    return None
