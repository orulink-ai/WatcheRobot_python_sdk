from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from typing import Any

import pytest
from websockets.asyncio.server import serve

from watcherobot.runtime.daemon.application.client import (
    ApplicationCommunicators,
    _local_connect_options,
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


def test_local_connect_options_match_the_installed_websockets_api() -> None:
    from websockets.asyncio.client import connect

    options = _local_connect_options(connect)

    assert options["max_size"] is None
    assert ("proxy" in options) is (
        "proxy" in inspect.signature(connect).parameters
    )


def test_communicators_connect_to_local_channels_with_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the installed websockets package against real local servers."""

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")

    async def scenario() -> None:
        async def hold_open(connection: Any) -> None:
            await connection.wait_closed()

        async with (
            serve(hold_open, "127.0.0.1", 0) as desktop_server,
            serve(hold_open, "127.0.0.1", 0) as device_server,
        ):
            desktop_port = desktop_server.sockets[0].getsockname()[1]
            device_port = device_server.sockets[0].getsockname()[1]
            connected = asyncio.Event()

            async def on_connected() -> None:
                connected.set()

            communicators = ApplicationCommunicators(
                desktop_url=f"ws://127.0.0.1:{desktop_port}",
                device_url=f"ws://127.0.0.1:{device_port}",
                on_connected=on_connected,
            )
            task = asyncio.create_task(communicators.run())
            await asyncio.wait_for(connected.wait(), timeout=2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_communicators_receive_while_connected_callback_waits(
    monkeypatch,
) -> None:
    connect_calls: list[tuple[str, dict[str, Any]]] = []

    class FakeConnection:
        def __init__(self, frames: list[str]) -> None:
            self._frames = frames

        def __aiter__(self) -> AsyncIterator[str]:
            async def iterate() -> AsyncIterator[str]:
                for frame in self._frames:
                    await asyncio.sleep(0)
                    yield frame
                await asyncio.Event().wait()

            return iterate()

        async def send(self, _frame: str | bytes) -> None:
            return None

    class FakeContext:
        def __init__(self, connection: FakeConnection) -> None:
            self._connection = connection

        async def __aenter__(self) -> FakeConnection:
            return self._connection

        async def __aexit__(self, *_args: Any) -> None:
            return None

    desktop = FakeConnection([])
    device = FakeConnection(["ready"])

    def fake_connect(
        url: str,
        *,
        max_size: int | None = 2**20,
        proxy: str | bool | None = True,
    ) -> FakeContext:
        connect_calls.append((url, {"max_size": max_size, "proxy": proxy}))
        return FakeContext(desktop if url.endswith("desktop") else device)

    monkeypatch.setattr(
        "watcherobot.runtime.daemon.application.client.connect",
        fake_connect,
    )

    async def scenario() -> None:
        ready = asyncio.Event()

        async def on_frame(
            channel: ApplicationChannel,
            frame: str | bytes,
        ) -> None:
            if channel is ApplicationChannel.DEVICE and frame == "ready":
                ready.set()

        async def on_connected() -> None:
            await asyncio.wait_for(ready.wait(), timeout=0.1)

        communicators = ApplicationCommunicators(
            desktop_url="ws://test/desktop",
            device_url="ws://test/device",
            on_frame=on_frame,
            on_connected=on_connected,
        )
        task = asyncio.create_task(communicators.run())
        await asyncio.wait_for(ready.wait(), timeout=0.2)
        task.cancel()
        done, pending = await asyncio.wait({task}, timeout=0.5)
        assert pending == set()
        assert done == {task}
        await asyncio.gather(*done, return_exceptions=True)

    asyncio.run(scenario())
    assert connect_calls == [
        ("ws://test/desktop", {"max_size": None, "proxy": None}),
        ("ws://test/device", {"max_size": None, "proxy": None}),
    ]
