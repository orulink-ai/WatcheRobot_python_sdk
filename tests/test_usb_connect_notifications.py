"""USB attach transaction regressions; no serial ports or servers are opened."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from watcherobot.runtime.daemon.application.session import ApplicationChannel
from watcherobot.runtime.daemon.connections.registry import ExternalClientRole
from watcherobot.runtime.daemon.pairing.session import PairingSessionError
from watcherobot.runtime.daemon.runtime import DaemonRuntime


class MemoryUsb:
    """Model the transport-owned, shielded close and its callback error."""

    def __init__(self, port, baudrate, *, on_frame, on_disconnect):
        self.port = port
        self.baudrate = baudrate
        self.on_frame = on_frame
        self.on_disconnect = on_disconnect
        self.started = False
        self.closed = False
        self.start_error = None
        self.close_error = None
        self.cleanup = None

    @property
    def is_open(self):
        return self.started and not self.closed

    async def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    async def close(self, **_kwargs):
        async def cleanup():
            self.closed = True
            try:
                await self.on_disconnect(None)
            except (Exception, asyncio.CancelledError) as exc:
                self.close_error = exc

        if self.cleanup is None:
            self.cleanup = asyncio.create_task(cleanup())
        await asyncio.shield(self.cleanup)
        if self.close_error is not None:
            raise self.close_error


@pytest.fixture
def usb_runtime(tmp_path, monkeypatch):
    import watcherobot.runtime.daemon.runtime as module

    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    devices = []
    failures = SimpleNamespace(start=None)

    def factory(port, baudrate, **callbacks):
        usb = MemoryUsb(port, baudrate, **callbacks)
        usb.start_error = failures.start
        devices.append(usb)
        return usb

    monkeypatch.setattr(module, "UsbConnection", factory)
    runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
    return runtime, devices, failures


def add_desktop(runtime, send):
    class Desktop:
        async def send(self, frame):
            await send(json.loads(frame))

    peer = runtime.connection_registry.add(Desktop())
    runtime.connection_registry.declare_role(peer, role="desktop")


def add_application(runtime, monkeypatch, send):
    async def send_frame(channel, frame):
        assert channel is ApplicationChannel.DEVICE
        await send(json.loads(frame))

    monkeypatch.setattr(runtime.application, "registry", SimpleNamespace(active_run=object()))
    monkeypatch.setattr(runtime.application.bridge, "send_to_application", send_frame)


@pytest.mark.parametrize("failure_at", ["start", "online"])
@pytest.mark.parametrize("offline_failure", [None, "desktop", "application", "both", "desktop_cancel", "application_cancel"])
def test_failed_attach_retires_owned_usb_and_preserves_original_error(
    usb_runtime, monkeypatch, failure_at, offline_failure,
):
    runtime, devices, failures = usb_runtime
    original = OSError("USB open failed") if failure_at == "start" else ConnectionError("online send failed")
    desktop_states, application_states = [], []
    armed = True

    async def desktop(event):
        online = event["data"]["online"]
        desktop_states.append(online)
        if armed and online and failure_at == "online":
            raise original
        if armed and not online and offline_failure == "desktop_cancel":
            raise asyncio.CancelledError("offline Desktop cancelled")
        if armed and not online and offline_failure in ("desktop", "both"):
            raise RuntimeError("Desktop offline send failed")

    async def application(event):
        application_states.append(event["data"]["online"])
        if armed and offline_failure == "application_cancel":
            raise asyncio.CancelledError("offline App cancelled")
        if armed and offline_failure in ("application", "both"):
            raise RuntimeError("App offline send failed")

    add_desktop(runtime, desktop)
    add_application(runtime, monkeypatch, application)

    async def run():
        nonlocal armed
        if failure_at == "start":
            failures.start = original
        with pytest.raises(type(original)) as caught:
            await runtime.connect_usb_device("memory-first")
        assert caught.value is original
        usb = devices[0]
        assert usb.closed
        assert runtime._usb_connection is None
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0
        assert desktop_states == ([False] if failure_at == "start" else [True, False])
        assert application_states == ([] if offline_failure == "desktop_cancel" else [False])

        armed = False
        failures.start = None
        status = await runtime.connect_usb_device("memory-retry")
        replacement = devices[1]
        assert status["device"]["online"] is True
        # A late callback for the retired attempt cannot retire its replacement.
        await usb.on_disconnect(None)
        assert runtime._usb_connection is replacement
        assert replacement.is_open
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 1
        assert await runtime.disconnect_usb_device()

    asyncio.run(run())


@pytest.mark.parametrize("cancel_at", ["callback", "request"])
@pytest.mark.parametrize("offline_fails", [None, "error", "cancel"])
def test_cancelled_online_publication_rolls_back_and_allows_retry(
    usb_runtime, monkeypatch, cancel_at, offline_fails,
):
    runtime, devices, _ = usb_runtime
    cancellation = asyncio.CancelledError("online notification cancelled")
    desktop_states, application_states = [], []
    armed = True

    async def run():
        nonlocal armed
        entered = asyncio.Event()
        hold_online = asyncio.Event()

        async def desktop(event):
            online = event["data"]["online"]
            desktop_states.append(online)
            if armed and online:
                if cancel_at == "callback":
                    raise cancellation
                entered.set()
                await hold_online.wait()
            if armed and not online and offline_fails == "error":
                raise ConnectionError("offline Desktop failure")

        async def application(event):
            application_states.append(event["data"]["online"])
            if armed and offline_fails == "cancel":
                raise asyncio.CancelledError("offline App cancelled")
            if armed and offline_fails == "error":
                raise RuntimeError("offline App failure")

        add_desktop(runtime, desktop)
        add_application(runtime, monkeypatch, application)
        if cancel_at == "callback":
            with pytest.raises(asyncio.CancelledError) as caught:
                await runtime.connect_usb_device("memory-first")
            assert caught.value is cancellation
        else:
            attaching = asyncio.create_task(runtime.connect_usb_device("memory-first"))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                attaching.cancel("online request cancelled")
                with pytest.raises(asyncio.CancelledError, match="online request cancelled"):
                    await asyncio.wait_for(attaching, 1)
            finally:
                hold_online.set()
                await asyncio.gather(attaching, return_exceptions=True)

        assert devices[0].closed
        assert runtime._usb_connection is None
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0
        assert desktop_states == [True, False]
        assert application_states == [False]
        armed = False
        status = await runtime.connect_usb_device("memory-retry")
        assert status["device"]["online"] is True
        assert await runtime.disconnect_usb_device()

    asyncio.run(run())


@pytest.mark.parametrize("cancel_rollback", [False, True])
def test_failed_online_attach_reserves_slot_through_owned_offline_cleanup(
    usb_runtime, monkeypatch, cancel_rollback,
):
    runtime, devices, _ = usb_runtime
    original = ConnectionError("online send failed")
    armed = True

    async def run():
        nonlocal armed
        entered, release = asyncio.Event(), asyncio.Event()
        application_states = []

        async def desktop(event):
            if armed and event["data"]["online"]:
                raise original

        async def application(event):
            application_states.append(event["data"]["online"])
            if armed:
                entered.set()
                await release.wait()

        add_desktop(runtime, desktop)
        add_application(runtime, monkeypatch, application)
        attaching = asyncio.create_task(runtime.connect_usb_device("memory-first"))
        retry = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            usb = devices[0]
            assert runtime._usb_connection is usb
            assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0
            with pytest.raises(PairingSessionError, match="occupied"):
                await runtime.pair_device("123456", "sdk")
            retry = asyncio.create_task(runtime.connect_usb_device("memory-retry"))
            await asyncio.sleep(0)
            assert not retry.done()
            if cancel_rollback:
                attaching.cancel("rollback caller cancelled")
                with pytest.raises(asyncio.CancelledError, match="rollback caller cancelled"):
                    await asyncio.wait_for(attaching, 1)
                with pytest.raises(PairingSessionError, match="occupied"):
                    await asyncio.wait_for(retry, 1)
                assert runtime._usb_connection is usb
            armed = False
            release.set()
            if cancel_rollback:
                await asyncio.wait_for(usb.close(), 1)
                status = await runtime.connect_usb_device("memory-retry")
            else:
                with pytest.raises(ConnectionError) as caught:
                    await asyncio.wait_for(attaching, 1)
                assert caught.value is original
                status = await asyncio.wait_for(retry, 1)
            assert application_states == [False]
            assert status["device"]["online"] is True
            assert runtime._usb_connection is devices[1]
            assert devices[1].is_open
            assert await runtime.disconnect_usb_device()
        finally:
            armed = False
            release.set()
            pending = [attaching] + ([retry] if retry is not None else [])
            await asyncio.gather(*pending, return_exceptions=True)
            for usb in devices:
                if usb.cleanup is not None:
                    await asyncio.gather(usb.cleanup, return_exceptions=True)

    asyncio.run(run())
