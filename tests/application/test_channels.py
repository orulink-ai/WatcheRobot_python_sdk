from __future__ import annotations

from watcherobot.application import (
    ApplicationChannel,
    ApplicationChannels,
)


def test_public_application_channels_build_from_daemon_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "WATCHER_APP_DESKTOP_WS_URL",
        "ws://127.0.0.1:9001/desktop",
    )
    monkeypatch.setenv(
        "WATCHER_APP_DEVICE_WS_URL",
        "ws://127.0.0.1:9001/device",
    )

    channels = ApplicationChannels.from_environment()

    assert channels.desktop_url == "ws://127.0.0.1:9001/desktop"
    assert channels.device_url == "ws://127.0.0.1:9001/device"
    assert ApplicationChannel.DEVICE.value == "device"
