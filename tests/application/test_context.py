from __future__ import annotations

import asyncio
import json

import pytest

from watcherobot.application import (
    ApplicationContext,
    ApplicationEnvironmentError,
)
from watcherobot.runtime.daemon.application.bridge import (
    LocalWebSocketApplicationBridge,
)
from watcherobot.runtime.daemon.application.session import (
    ApplicationChannel,
    ApplicationSessionRegistry,
)


class _TransportStub:
    capabilities: tuple[str, ...] = ()
    device_info: dict = {}
    resource_snapshot: dict = {}
    resource_baseline: dict = {}
    resource_rtc_baseline: dict = {}
    resource_history: list = []

    def set_callbacks(self, *_callbacks) -> None:
        return None

    def set_desktop_callback(self, _callback) -> None:
        return None

    def add_message_listener(self, _callback) -> None:
        return None

    def remove_message_listener(self, _callback) -> None:
        return None

    def close(self) -> None:
        return None


def test_application_context_observes_daemon_shutdown_signal(
    tmp_path,
    monkeypatch,
) -> None:
    signal = tmp_path / "application.stop"
    monkeypatch.setenv("WATCHER_APP_SHUTDOWN_SIGNAL", str(signal))
    context = ApplicationContext(
        app_id="test_app",
        transport=_TransportStub(),
    )

    assert context.shutdown_requested is False
    signal.touch()
    assert context.shutdown_requested is True


def test_context_rejects_processes_without_daemon_application_environment(
    monkeypatch,
) -> None:
    for name in (
        "WATCHER_APP_ID",
        "WATCHER_APP_RUN_CREDENTIAL",
        "WATCHER_APP_DESKTOP_WS_URL",
        "WATCHER_APP_DEVICE_WS_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(
        ApplicationEnvironmentError,
        match="WATCHER_APP_DEVICE_WS_URL",
    ):
        ApplicationContext.from_environment()


def test_context_routes_robot_and_desktop_through_authorized_channels(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        registry = ApplicationSessionRegistry(current_app="test_app")
        run = registry.begin_start()
        received: list[tuple[ApplicationChannel, str | bytes]] = []
        bridge: LocalWebSocketApplicationBridge

        async def on_frame(
            channel: ApplicationChannel,
            frame: str | bytes,
        ) -> None:
            received.append((channel, frame))
            if channel is not ApplicationChannel.DEVICE:
                return
            assert isinstance(frame, str)
            command = json.loads(frame)
            await bridge.send_to_application(
                ApplicationChannel.DEVICE,
                json.dumps(
                    {
                        "type": "sys.ack",
                        "code": 0,
                        "data": {
                            "command_id": command["data"]["command_id"],
                            "accepted": True,
                        },
                    }
                ),
            )

        bridge = LocalWebSocketApplicationBridge(
            registry=registry,
            on_frame=on_frame,
        )
        await bridge.start()
        monkeypatch.setenv("WATCHER_APP_ID", "test_app")
        monkeypatch.setenv("WATCHER_APP_RUN_CREDENTIAL", run.credential)
        monkeypatch.setenv(
            "WATCHER_APP_DESKTOP_WS_URL",
            bridge.channel_url(
                ApplicationChannel.DESKTOP,
                credential=run.credential,
            ),
        )
        monkeypatch.setenv(
            "WATCHER_APP_DEVICE_WS_URL",
            bridge.channel_url(
                ApplicationChannel.DEVICE,
                credential=run.credential,
            ),
        )

        try:
            async with ApplicationContext.from_environment() as app:
                response = await asyncio.to_thread(
                    app.robot._command,
                    "ctrl.test",
                    {"value": 7},
                )
                assert response["data"]["accepted"] is True

                await bridge.send_to_application(
                    ApplicationChannel.DESKTOP,
                    "desktop-to-app",
                )
                assert await app.desktop.receive(timeout=1) == "desktop-to-app"

                await app.desktop.send("app-to-desktop")
                for _ in range(100):
                    if (
                        ApplicationChannel.DESKTOP,
                        "app-to-desktop",
                    ) in received:
                        break
                    await asyncio.sleep(0.01)
                assert (
                    ApplicationChannel.DESKTOP,
                    "app-to-desktop",
                ) in received
                assert app.logger.name.endswith("test_app")
        finally:
            await bridge.stop()

    asyncio.run(scenario())
