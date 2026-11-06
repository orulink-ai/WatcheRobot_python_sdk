import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from watcherobot.runtime.daemon.runtime import DaemonRuntime
from watcherobot.runtime.daemon.connections.registry import ExternalClientRole
from watcherobot.runtime.daemon.control.rest import DaemonControlAPI
from watcherobot.runtime.daemon.pairing.protocol import HardwareHello, PairAccept
from watcherobot.runtime.daemon.pairing.session import PairingSessionError


@pytest.fixture(autouse=True)
def isolate_runtime_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))


class FakeUsb:
    def __init__(self,port,baudrate,*,on_frame,on_disconnect,**kwargs):
        self.port=port;self.baudrate=baudrate;self.on_frame=on_frame;self.on_disconnect=on_disconnect
        self.frames=[];self.closed=False
    @property
    def is_open(self): return not self.closed
    async def start(self): pass
    async def send(self,frame): self.frames.append(frame)
    async def close(self,**kwargs):
        if not self.closed:
            self.closed=True
            await self.on_disconnect(None)

def test_usb_occupies_single_slot_and_disconnect_releases_registry(tmp_path,monkeypatch):
    import watcherobot.runtime.daemon.runtime as module
    monkeypatch.setattr(module,'UsbConnection',FakeUsb)
    async def run():
        runtime=DaemonRuntime(application_dir=tmp_path,current_app=None,external_port=0,control_port=0,pairing_udp_port=0)
        status=await runtime.connect_usb_device('COM99',1000000)
        assert status['device']['online'] and status['device']['transport']=='usb'
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE)==1
        with pytest.raises(Exception,match='occupied'):
            await runtime.connect_usb_device('COM100',1000000)
        with pytest.raises(Exception,match='occupied'):
            await runtime.pair_device('123456','sdk')
        assert await runtime.disconnect_device()
        assert not runtime.device_status()['device']['online']
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE)==0
    asyncio.run(run())


def test_usb_disconnect_endpoint_preserves_connected_lan_session(tmp_path, monkeypatch):
    async def run():
        runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
        session = runtime.device_pairing
        session.start_pairing(
            pairing_code="123456", target_mode="python_sdk", websocket_port=8765, now=10,
        )
        request = session.current_request
        token = "a" * 64  # Test-only session identity; no device connection.
        session.accept_device(PairAccept(
            request_id=request.request_id, daemon_instance_id=session.daemon_instance_id,
            target_mode="python_sdk", session_token=token,
        ), peer_ip="127.0.0.1", now=11)
        session.connect_device(HardwareHello(
            pair_request_id=request.request_id, daemon_instance_id=session.daemon_instance_id,
            mode="python_sdk", session_token=token,
        ), peer_ip="127.0.0.1", now=12)

        class LanPeer:
            closed = False
            frames = []

            async def send(self, frame):
                self.frames.append(frame)

            async def close(self, **kwargs):
                self.closed = True

        peer = LanPeer()
        connection = runtime.connection_registry.add(peer)
        runtime.connection_registry.declare_role(connection, role="hardware")
        before = session.snapshot()
        monkeypatch.setattr(runtime, "_forget_device_binding", lambda *_: pytest.fail("LAN binding removed"))
        api = DaemonControlAPI(controller=runtime).create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
            for _ in range(2):
                response = await client.post("/daemon/devices/usb/disconnect")
                assert response.status_code == 200
                assert response.json()["disconnected"] is False
                assert response.json()["device"]["online"] is True
        assert session.snapshot() == before
        assert not peer.closed and peer.frames == []
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 1

    asyncio.run(run())


def test_usb_disconnect_endpoint_is_idempotent(tmp_path, monkeypatch):
    import watcherobot.runtime.daemon.runtime as module
    monkeypatch.setattr(module, "UsbConnection", FakeUsb)

    async def run():
        runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
        await runtime.connect_usb_device("COM99")
        usb = runtime._usb_connection
        api = DaemonControlAPI(controller=runtime).create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
            for expected in (True, False):
                response = await client.post("/daemon/devices/usb/disconnect")
                assert response.status_code == 200
                assert response.json()["disconnected"] is expected
                assert response.json()["device"]["online"] is False
        assert usb.closed
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0

    asyncio.run(run())


@pytest.mark.parametrize("failed_open,notification_error", [(False, False), (False, True), (True, False)])
def test_usb_reattach_waits_for_old_offline_notification(
    tmp_path, monkeypatch, failed_open, notification_error,
):
    import watcherobot.runtime.daemon.runtime as module

    class Usb(FakeUsb):
        async def start(self):
            if failed_open and self.port == "COM99":
                raise OSError("open failed")

    monkeypatch.setattr(module, "UsbConnection", Usb)

    async def run():
        runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
        entered, release = asyncio.Event(), asyncio.Event()
        notifications = []

        async def notify(_channel, frame):
            entered.set()
            await release.wait()
            notifications.append(json.loads(frame)["data"]["online"])
            if notification_error:
                raise RuntimeError("notification failed")

        monkeypatch.setattr(runtime.application, "registry", SimpleNamespace(active_run=object()))
        monkeypatch.setattr(runtime.application.bridge, "send_to_application", notify)
        if failed_open:
            closing = asyncio.create_task(runtime.connect_usb_device("COM99"))
        else:
            await runtime.connect_usb_device("COM99")
            closing = asyncio.create_task(runtime.disconnect_device())
        queued_attach = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if failed_open:
                # Failed startup still owns the attach lock. A new request
                # waits for cleanup rather than overtaking its offline event.
                queued_attach = asyncio.create_task(runtime.connect_usb_device("COM100"))
                await asyncio.sleep(0)
                assert not queued_attach.done()
            else:
                with pytest.raises(PairingSessionError, match="occupied"):
                    await asyncio.wait_for(runtime.connect_usb_device("COM100"), 1)
        finally:
            release.set()
            error = OSError if failed_open else RuntimeError if notification_error else None
            if error is None:
                await asyncio.wait_for(closing, 1)
            else:
                with pytest.raises(error):
                    await asyncio.wait_for(closing, 1)
        assert notifications == [False]
        if queued_attach is None:
            assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0
            status = await runtime.connect_usb_device("COM100")
        else:
            status = await asyncio.wait_for(queued_attach, 1)
        assert status["device"]["online"] is True
        # The port can be released after either a successful or failed callback.
        if notification_error:
            with pytest.raises(RuntimeError, match="notification failed"):
                await runtime.disconnect_device()
        else:
            assert await runtime.disconnect_device()

    asyncio.run(run())

def test_cancelled_failed_attach_keeps_slot_until_shielded_cleanup_finishes(tmp_path, monkeypatch):
    import watcherobot.runtime.daemon.runtime as module

    class Usb(FakeUsb):
        cleanup = None

        async def start(self):
            if self.port == "COM99":
                raise OSError("open failed")

        async def close(self, **kwargs):
            # Real UsbConnection shields its owned cleanup task from callers.
            if self.cleanup is None:
                self.cleanup = asyncio.create_task(super().close(**kwargs))
            await asyncio.shield(self.cleanup)

    monkeypatch.setattr(module, "UsbConnection", Usb)

    async def run():
        runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
        entered, release = asyncio.Event(), asyncio.Event()

        async def publish(snapshot):
            if not snapshot["online"]:
                entered.set()
                await release.wait()

        monkeypatch.setattr(runtime, "_publish_device_state", publish)
        attaching = asyncio.create_task(runtime.connect_usb_device("COM99"))
        await asyncio.wait_for(entered.wait(), 1)
        usb = runtime._usb_connection
        try:
            attaching.cancel()
            with pytest.raises(asyncio.CancelledError):
                await attaching
            with pytest.raises(PairingSessionError, match="occupied"):
                await asyncio.wait_for(runtime.connect_usb_device("COM100"), 1)
        finally:
            release.set()
            await asyncio.wait_for(usb.close(), 1)
            if runtime._usb_connection is not None:
                await runtime.disconnect_device()
        assert runtime._usb_connection is None

    asyncio.run(run())


def test_usb_payload_new_types_and_binary_are_opaque(tmp_path,monkeypatch):
    import watcherobot.runtime.daemon.runtime as module
    monkeypatch.setattr(module,'UsbConnection',FakeUsb)
    async def run():
        runtime=DaemonRuntime(application_dir=tmp_path,current_app=None)
        await runtime.connect_usb_device('COM99',1000000)
        frames=[]
        class Desktop:
            async def send(self,frame): frames.append(frame)
        desktop=runtime.connection_registry.add(Desktop())
        runtime.connection_registry.declare_role(desktop,role='desktop')
        usb=runtime._usb_connection
        payloads=['{"type":"brand.new.business","data":{"x":4}}',b'WSPK\x08opaque']
        for payload in payloads: await usb.on_frame(payload)
        assert frames==payloads
        await runtime.disconnect_device()
    asyncio.run(run())


@pytest.mark.parametrize("desktop_fails,application_fails", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_usb_offline_notifies_both_recipients_despite_send_errors(
    tmp_path, monkeypatch, desktop_fails, application_fails,
):
    import watcherobot.runtime.daemon.runtime as module
    from watcherobot.runtime.daemon.application.session import ApplicationChannel

    monkeypatch.setattr(module, "UsbConnection", FakeUsb)

    async def run():
        runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
        desktop_error = ConnectionError("Desktop offline notification failed")
        application_error = RuntimeError("Application offline notification failed")
        desktop_notifications, application_notifications = [], []

        class Desktop:
            async def send(self, frame):
                event = json.loads(frame)
                if event["data"]["online"] is False:
                    desktop_notifications.append(event)
                    if desktop_fails:
                        raise desktop_error

        async def notify_application(channel, frame):
            assert channel is ApplicationChannel.DEVICE
            application_notifications.append(json.loads(frame))
            if application_fails:
                raise application_error

        desktop = runtime.connection_registry.add(Desktop())
        runtime.connection_registry.declare_role(desktop, role="desktop")
        monkeypatch.setattr(runtime.application, "registry", SimpleNamespace(active_run=object()))
        monkeypatch.setattr(runtime.application.bridge, "send_to_application", notify_application)
        await runtime.connect_usb_device("COM99")
        usb = runtime._usb_connection
        actual_error = None
        try:
            await runtime.disconnect_usb_device()
        except Exception as exc:
            actual_error = exc

        assert len(desktop_notifications) == 1
        assert len(application_notifications) == 1
        for event in desktop_notifications + application_notifications:
            assert event["type"] == "daemon.device.state"
            assert event["data"]["online"] is False
        expected_error = application_error if application_fails else desktop_error if desktop_fails else None
        assert actual_error is expected_error
        assert usb.closed
        assert runtime._usb_connection is None
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0

    asyncio.run(run())


def test_usb_desktop_error_keeps_slot_until_application_offline_delivery(tmp_path, monkeypatch):
    import watcherobot.runtime.daemon.runtime as module

    monkeypatch.setattr(module, "UsbConnection", FakeUsb)

    async def run():
        runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
        entered, release = asyncio.Event(), asyncio.Event()

        class Desktop:
            async def send(self, frame):
                if json.loads(frame)["data"]["online"] is False:
                    raise ConnectionError("Desktop went away")

        async def notify_application(_channel, frame):
            assert json.loads(frame)["data"]["online"] is False
            entered.set()
            await release.wait()

        desktop = runtime.connection_registry.add(Desktop())
        runtime.connection_registry.declare_role(desktop, role="desktop")
        monkeypatch.setattr(runtime.application, "registry", SimpleNamespace(active_run=object()))
        monkeypatch.setattr(runtime.application.bridge, "send_to_application", notify_application)
        await runtime.connect_usb_device("COM99")
        closing = asyncio.create_task(runtime.disconnect_usb_device())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            with pytest.raises(PairingSessionError, match="occupied"):
                await asyncio.wait_for(runtime.connect_usb_device("COM100"), 1)
        finally:
            release.set()
            with pytest.raises(ConnectionError, match="Desktop went away"):
                await asyncio.wait_for(closing, 1)

        assert runtime._usb_connection is None
        status = await runtime.connect_usb_device("COM100")
        assert status["device"]["online"] is True
        with pytest.raises(ConnectionError, match="Desktop went away"):
            await runtime.disconnect_usb_device()
        assert runtime._usb_connection is None

    asyncio.run(run())


@pytest.mark.parametrize("cancel_at", ["desktop", "application", "application_after_desktop_error"])
def test_usb_offline_cancellation_propagates_and_releases_slot(tmp_path, monkeypatch, cancel_at):
    import watcherobot.runtime.daemon.runtime as module

    monkeypatch.setattr(module, "UsbConnection", FakeUsb)

    async def run():
        runtime = DaemonRuntime(application_dir=tmp_path, current_app=None)
        cancellation = asyncio.CancelledError("offline notification cancelled")
        application_notifications = []

        class Desktop:
            async def send(self, frame):
                if json.loads(frame)["data"]["online"] is False:
                    if cancel_at == "desktop":
                        raise cancellation
                    if cancel_at == "application_after_desktop_error":
                        raise ConnectionError("Desktop went away")

        async def notify_application(_channel, frame):
            application_notifications.append(json.loads(frame))
            raise cancellation

        desktop = runtime.connection_registry.add(Desktop())
        runtime.connection_registry.declare_role(desktop, role="desktop")
        monkeypatch.setattr(runtime.application, "registry", SimpleNamespace(active_run=object()))
        monkeypatch.setattr(runtime.application.bridge, "send_to_application", notify_application)
        await runtime.connect_usb_device("COM99")
        usb = runtime._usb_connection
        with pytest.raises(asyncio.CancelledError) as caught:
            await runtime.disconnect_usb_device()
        assert caught.value is cancellation
        assert len(application_notifications) == (0 if cancel_at == "desktop" else 1)
        assert usb.closed
        assert runtime._usb_connection is None
        assert runtime.connection_registry.online_count(ExternalClientRole.DEVICE) == 0

    asyncio.run(run())
