"""Low-level channels for advanced Daemon-managed Applications."""

from __future__ import annotations

from watcherobot.runtime.daemon.application.client import (
    ApplicationCommunicators,
    Frame,
    FrameCallback,
)
from watcherobot.runtime.daemon.application.session import ApplicationChannel


class ApplicationChannels(ApplicationCommunicators):
    """Expose source-aware desktop/device frames to an advanced Application.

    Most Applications should use :class:`ApplicationContext`. This lower-level
    API exists for Applications such as ``watcher_default`` that already own a
    complete business protocol stack and need the original frames unchanged.
    """

    @property
    def desktop_url(self) -> str:
        return self._desktop_url

    @property
    def device_url(self) -> str:
        return self._device_url


__all__ = [
    "ApplicationChannel",
    "ApplicationChannels",
    "Frame",
    "FrameCallback",
]
