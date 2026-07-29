from __future__ import annotations

import asyncio

from websockets.asyncio.client import connect

from watcherobot.runtime.daemon.application.bridge import (
    LocalWebSocketApplicationBridge,
)
from watcherobot.runtime.daemon.application.session import (
    ApplicationChannel,
    ApplicationSessionRegistry,
    ApplicationState,
)


def test_local_bridge_keeps_channel_source_and_raw_frame_content() -> None:
    async def scenario() -> None:
        received: asyncio.Queue[tuple[ApplicationChannel, str | bytes]] = (
            asyncio.Queue()
        )
        lost_channels: asyncio.Queue[ApplicationChannel] = asyncio.Queue()
        registry = ApplicationSessionRegistry(current_app="watcher_default")
        run = registry.begin_start()
        bridge = LocalWebSocketApplicationBridge(
            registry=registry,
            on_frame=lambda channel, frame: received.put((channel, frame)),
            on_channel_lost=lost_channels.put,
        )
        await bridge.start()

        try:
            async with (
                connect(
                    bridge.channel_url(
                        ApplicationChannel.DESKTOP,
                        credential=run.credential,
                    )
                ) as desktop,
                connect(
                    bridge.channel_url(
                        ApplicationChannel.DEVICE,
                        credential=run.credential,
                    )
                ) as device,
            ):
                assert run.state is ApplicationState.RUNNING

                desktop_text = '{"type":"cmd.desktop","data":{"value":1}}'
                device_binary = b"\x00\x10\x20\xff"
                await desktop.send(desktop_text)
                await device.send(device_binary)

                assert await asyncio.wait_for(received.get(), timeout=1) == (
                    ApplicationChannel.DESKTOP,
                    desktop_text,
                )
                assert await asyncio.wait_for(received.get(), timeout=1) == (
                    ApplicationChannel.DEVICE,
                    device_binary,
                )

                device_text = '{"type":"evt.device","data":{"online":true}}'
                desktop_binary = b"\xaa\xbb\xcc"
                await bridge.send_to_application(
                    ApplicationChannel.DEVICE,
                    device_text,
                )
                await bridge.send_to_application(
                    ApplicationChannel.DESKTOP,
                    desktop_binary,
                )

                assert await asyncio.wait_for(device.recv(), timeout=1) == device_text
                assert await asyncio.wait_for(desktop.recv(), timeout=1) == desktop_binary

                duplicate = await connect(
                    bridge.channel_url(
                        ApplicationChannel.DESKTOP,
                        credential=run.credential,
                    )
                )
                await asyncio.wait_for(duplicate.wait_closed(), timeout=1)
                assert duplicate.close_code == bridge.CLOSE_CHANNEL_OCCUPIED

                await device.close()
                assert await asyncio.wait_for(lost_channels.get(), timeout=1) is (
                    ApplicationChannel.DEVICE
                )
                assert run.state is ApplicationState.ERROR
        finally:
            await bridge.stop()

    asyncio.run(scenario())


def test_local_bridge_rejects_invalid_run_credential() -> None:
    async def scenario() -> None:
        registry = ApplicationSessionRegistry(current_app="watcher_default")
        registry.begin_start()
        bridge = LocalWebSocketApplicationBridge(registry=registry)
        await bridge.start()

        try:
            rejected = await connect(
                bridge.channel_url(
                    ApplicationChannel.DESKTOP,
                    credential="invalid",
                )
            )
            await asyncio.wait_for(rejected.wait_closed(), timeout=1)
            assert rejected.close_code == bridge.CLOSE_INVALID_CREDENTIAL
            assert registry.active_run is not None
            assert registry.active_run.connected_channels == set()
        finally:
            await bridge.stop()

    asyncio.run(scenario())
