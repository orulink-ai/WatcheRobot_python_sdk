from __future__ import annotations

import asyncio

from watcherobot.runtime.daemon.application.bridge import ApplicationBridge
from watcherobot.runtime.daemon.application.session import (
    ApplicationChannel,
    ApplicationSessionRegistry,
    ApplicationState,
)
from watcherobot.runtime.daemon.connections.registry import (
    ExternalClientRole,
    ExternalConnectionRegistry,
)
from watcherobot.runtime.daemon.routing.raw import RawFrameRouter


class RecordingWebSocket:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []

    async def send(self, frame: str | bytes) -> None:
        self.sent.append(frame)


class RecordingApplicationBridge(ApplicationBridge):
    def __init__(self) -> None:
        self.sent: list[tuple[ApplicationChannel, str | bytes]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_to_application(
        self,
        channel: ApplicationChannel,
        frame: str | bytes,
    ) -> None:
        self.sent.append((channel, frame))


def test_application_routes_device_frames_without_external_desktop() -> None:
    async def scenario() -> None:
        external = ExternalConnectionRegistry()
        device_socket = RecordingWebSocket()
        device = external.add(device_socket)
        external.declare_role(device, role="hardware")

        application = ApplicationSessionRegistry(current_app="com.orulink.demo")
        run = application.begin_start()
        application.attach_channel(
            ApplicationChannel.DESKTOP,
            credential=run.credential,
        )
        application.attach_channel(
            ApplicationChannel.DEVICE,
            credential=run.credential,
        )
        assert run.state is ApplicationState.RUNNING
        assert external.online_count(ExternalClientRole.DESKTOP) == 0

        bridge = RecordingApplicationBridge()
        router = RawFrameRouter(
            external,
            application_registry=application,
            application_bridge=bridge,
        )

        assert await router.route_application(
            ApplicationChannel.DEVICE,
            '{"type":"ctrl.light.off","code":0,"data":{}}',
        ) == 1
        assert device_socket.sent == [
            '{"type":"ctrl.light.off","code":0,"data":{}}'
        ]

        assert await router.route_external(
            device,
            '{"type":"evt.device.ready","code":0,"data":{}}',
        ) == 1
        assert bridge.sent == [
            (
                ApplicationChannel.DEVICE,
                '{"type":"evt.device.ready","code":0,"data":{}}',
            )
        ]

        application.end_run(ApplicationState.ENDED)
        assert await router.route_application(
            ApplicationChannel.DEVICE,
            '{"type":"ctrl.light.off","code":0,"data":{}}',
        ) == 0
        assert len(device_socket.sent) == 1

    asyncio.run(scenario())
