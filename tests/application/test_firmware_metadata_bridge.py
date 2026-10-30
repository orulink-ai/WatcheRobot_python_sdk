import asyncio
import json

from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.runtime.daemon.application.session import ApplicationChannel


def test_sdk_application_relays_firmware_query_through_its_device_channel():
    async def scenario():
        transport = DaemonApplicationTransport()
        sent, received = [], []

        class Channels:
            async def send(self, channel, frame):
                sent.append((channel, frame))

        transport._communicators = Channels()
        transport._desktop_callback = received.append
        query = json.dumps(
            {"type": "sys.sdk.ready.get", "code": 0, "data": {"command_id": "query"}}
        )
        await transport._on_frame(ApplicationChannel.DESKTOP, query)
        assert sent == [(ApplicationChannel.DEVICE, query)]
        command = json.dumps({"type": "ctrl.microphone.open", "code": 0, "data": {}})
        await transport._on_frame(ApplicationChannel.DESKTOP, command)
        assert received == [command]

    asyncio.run(scenario())


def test_ready_replaces_stale_version_and_is_published_via_application_desktop_channel():
    async def scenario():
        transport = DaemonApplicationTransport()
        sent = []

        class Channels:
            async def send(self, channel, frame):
                sent.append((channel, frame))

        transport._communicators = Channels()
        transport.device_info = {"firmware_version": "old", "device_id": "old"}
        frame = json.dumps(
            {"type": "evt.sdk.ready", "code": 0, "data": {"device_id": "new"}}
        )
        await transport._on_frame(ApplicationChannel.DEVICE, frame)
        assert "firmware_version" not in transport.device_info
        assert sent == [(ApplicationChannel.DESKTOP, frame)]

    asyncio.run(scenario())
