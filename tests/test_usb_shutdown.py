"""Daemon shutdown regressions using in-memory services and USB callbacks."""

import asyncio
import json

import pytest

from watcherobot.runtime.daemon.connections.registry import ExternalClientRole
from watcherobot.runtime.daemon.pairing.session import PairingSessionError
from watcherobot.runtime.daemon.runtime import DaemonRuntime


class MemoryUsb:
    def __init__(self, port, baudrate, *, on_frame, on_disconnect, attempts):
        self.port = port
        self.baudrate = baudrate
        self.on_disconnect = on_disconnect
        self.attempts = attempts
        self.closed = False
        self.cleanup = None
        self.close_reason = None

    @property
    def is_open(self):
        return not self.closed

    async def start(self):
        pass

    async def close(self, *, reason="closed"):
        async def cleanup():
            self.attempts.append("usb")
            self.close_reason = reason
            self.closed = True
            await self.on_disconnect(None)

        if self.cleanup is None:
            self.cleanup = asyncio.create_task(cleanup())
        # Match the transport's ownership: caller cancellation cannot cancel
        # the in-progress offline callback or release its slot prematurely.
        await asyncio.shield(self.cleanup)


@pytest.fixture
def shutdown_runtime(tmp_path, monkeypatch):
    import watcherobot.runtime.daemon.runtime as module

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    attempts, failures = [], {}

    def factory(port, baudrate, **callbacks):
        return MemoryUsb(port, baudrate, **callbacks, attempts=attempts)

    monkeypatch.setattr(module, "UsbConnection", factory)
    runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
    for name, service in (
        ("control", runtime.control_server),
        ("application", runtime.application),
        ("preview", runtime.preview_udp),
        ("pairing", runtime.pairing_udp),
        ("external", runtime.external_server),
    ):
        async def stop(component=name):
            attempts.append(component)
            if component in failures:
                raise failures[component]

        monkeypatch.setattr(service, "stop", stop)
    return runtime, attempts, failures


@pytest.mark.parametrize("preview_also_fails", [False, True])
def test_usb_offline_error_does_not_skip_shutdown(shutdown_runtime, preview_also_fails):
    runtime, attempts, failures = shutdown_runtime
    notification_error = ConnectionError("Desktop offline notification failed")
    preview_error = OSError("preview cleanup failed")
    if preview_also_fails:
        failures["preview"] = preview_error
    offline_notifications = []

    class Desktop:
        async def send(self, frame):
            event = json.loads(frame)
            if event["data"]["online"] is False:
                offline_notifications.append(event)
                raise notification_error

    desktop = runtime.connection_registry.add(Desktop())
    runtime.connection_registry.declare_role(desktop, role="desktop")

    async def run():
        await runtime.connect_usb_device("memory-only")
        usb = runtime._usb_connection
        with pytest.raises(ConnectionError) as caught:
            await runtime.stop()
        assert caught.value is notification_error
        assert attempts == ["control", "application", "usb", "preview", "pairing", "external"]
        assert len(offline_notifications) == 1
        assert usb.closed and usb.close_reason == "Daemon stopped"
        assert runtime._usb_connection is None
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0
        messages = [event["message"] for event in runtime.logs.recent()]
        assert any("ConnectionError" in message and str(notification_error) in message for message in messages)
        if preview_also_fails:
            assert any("OSError" in message and str(preview_error) in message for message in messages)

    asyncio.run(run())


@pytest.mark.parametrize("failed_component", ["control", "application", "preview", "pairing", "external"])
def test_shutdown_attempts_remaining_services_after_ordinary_error(shutdown_runtime, failed_component):
    runtime, attempts, failures = shutdown_runtime
    failure = RuntimeError(f"{failed_component} cleanup failed")
    failures[failed_component] = failure

    async def run():
        await runtime.connect_usb_device("memory-only")
        with pytest.raises(RuntimeError) as caught:
            await runtime.stop()
        assert caught.value is failure
        assert attempts == ["control", "application", "usb", "preview", "pairing", "external"]
        assert runtime._usb_connection is None
        messages = [event["message"] for event in runtime.logs.recent()]
        assert any("RuntimeError" in message and str(failure) in message for message in messages)

    asyncio.run(run())


@pytest.mark.parametrize("control_also_fails", [False, True])
def test_shutdown_offline_cancellation_is_not_replaced_by_ordinary_error(
    shutdown_runtime, control_also_fails,
):
    runtime, attempts, failures = shutdown_runtime
    if control_also_fails:
        failures["control"] = RuntimeError("control cleanup failed")

    class Desktop:
        async def send(self, frame):
            if json.loads(frame)["data"]["online"] is False:
                raise asyncio.CancelledError("offline delivery cancelled")

    desktop = runtime.connection_registry.add(Desktop())
    runtime.connection_registry.declare_role(desktop, role="desktop")

    async def run():
        await runtime.connect_usb_device("memory-only")
        with pytest.raises(asyncio.CancelledError):
            await runtime.stop()
        assert attempts == ["control", "application", "usb"]
        assert runtime._usb_connection is None
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0

    asyncio.run(run())


def test_cancelled_shutdown_keeps_usb_slot_until_owned_cleanup_finishes(shutdown_runtime, monkeypatch):
    runtime, attempts, failures = shutdown_runtime

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def publish(snapshot):
            if snapshot["online"] is False:
                entered.set()
                await release.wait()

        monkeypatch.setattr(runtime, "_publish_device_state", publish)
        await runtime.connect_usb_device("memory-only")
        usb = runtime._usb_connection
        stopping = asyncio.create_task(runtime.stop())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            with pytest.raises(PairingSessionError, match="occupied"):
                await runtime.connect_usb_device("another-memory-only")
            stopping.cancel()
            with pytest.raises(asyncio.CancelledError):
                await stopping
            assert attempts == ["control", "application", "usb"]
            assert runtime._usb_connection is usb
            with pytest.raises(PairingSessionError, match="occupied"):
                await runtime.connect_usb_device("another-memory-only")
        finally:
            release.set()
            await asyncio.wait_for(usb.close(), 1)
            await asyncio.gather(stopping, return_exceptions=True)
        assert runtime._usb_connection is None
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0

    asyncio.run(run())
