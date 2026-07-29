from __future__ import annotations

import asyncio

import pytest

from watcherobot.runtime.daemon.application.client import (
    ApplicationCommunicators,
)
from watcherobot.runtime.daemon.application.session import ApplicationChannel


def test_communicators_require_both_daemon_channel_urls(monkeypatch) -> None:
    monkeypatch.delenv("WATCHER_APP_DESKTOP_WS_URL", raising=False)
    monkeypatch.delenv("WATCHER_APP_DEVICE_WS_URL", raising=False)

    with pytest.raises(
        RuntimeError,
        match="Application communicator URLs are not configured",
    ):
        ApplicationCommunicators.from_environment()


def test_communicators_send_only_after_channel_is_connected() -> None:
    async def scenario() -> None:
        communicators = ApplicationCommunicators(
            desktop_url="ws://127.0.0.1:1",
            device_url="ws://127.0.0.1:2",
        )

        with pytest.raises(
            RuntimeError,
            match="Application communicator is not connected: device",
        ):
            await communicators.send(ApplicationChannel.DEVICE, b"frame")

    asyncio.run(scenario())
