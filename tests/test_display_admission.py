"""Display operations must retire before transport admission after observed offline."""
import asyncio
import json
import struct
import threading

import pytest

from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.desktop_session import DesktopRobotSession
from watcherobot.errors import WatcheRobotError
from watcherobot.robot import WatcheRobot
from watcherobot.runtime.daemon.application.session import ApplicationChannel


JPEG = b'\xff\xd8new content\xff\xd9'


class MemoryPeer:
    def __init__(self, kind, monkeypatch):
        self.kind = kind
        self.transport = (DaemonApplicationTransport(command_timeout=1)
            if kind == 'managed' else DesktopRobotSession('ws://unused.invalid', command_timeout=1))
        self.transport._loop = asyncio.get_running_loop()
        self.transport.capabilities = ('display.stream.v1',)
        self.robot = WatcheRobot._from_transport(self.transport)
        self.active = None
        self.commands = []
        self.images = []
        monkeypatch.setattr(self.transport, '_send', self.send)

    async def send(self, *args):
        frame = args[-1]
        if isinstance(frame, bytes):
            stream_id, sequence = struct.unpack_from('<HI', frame, 6)
            assert self.active == stream_id
            self.images.append(frame)
            self.robot._on_message({'type': 'evt.display.presented', 'data': {
                'stream_id': stream_id, 'sequence': sequence, 'device_sequence': 3, 'code': 0}})
            return
        command = json.loads(frame)
        kind, data = command['type'], command['data']
        self.commands.append(kind)
        if kind == 'ctrl.display.open':
            self.active = 41
            ack = {'stream_id': 41, 'max_frame_bytes': 65536}
        else:
            assert kind == 'ctrl.display.close'
            self.active = None
            ack = {}
        self.transport._pending[data['command_id']].set_result({'type': 'sys.ack', 'data': ack})

    async def offline(self):
        event = {'type': 'daemon.device.state', 'data': {'online': False}}
        if self.kind == 'managed':
            await self.transport._on_frame(ApplicationChannel.DEVICE, json.dumps(event))
        else:
            self.robot._on_message(event)
        self.active = None


@pytest.mark.parametrize('kind', ['managed', 'desktop'])
@pytest.mark.parametrize('delay', ['before_submit', 'after_submit'])
@pytest.mark.parametrize('retire', ['offline', 'domain_close'])
def test_open_reservation_rechecked_on_transport_admission(monkeypatch, kind, delay, retire):
    async def scenario():
        peer = MemoryPeer(kind, monkeypatch)
        reached = threading.Event()
        release_thread = threading.Event()
        release_loop = asyncio.Event()
        submit = peer.transport._submit

        def delayed_submit(coroutine):
            if coroutine.cr_frame.f_locals.get('message_type') != 'ctrl.display.open':
                return submit(coroutine)
            if delay == 'before_submit':
                reached.set()
                assert release_thread.wait(3)
                return submit(coroutine)

            async def deferred():
                try:
                    reached.set()
                    await release_loop.wait()
                    return await coroutine
                finally:
                    coroutine.close()
            return submit(deferred())

        monkeypatch.setattr(peer.transport, '_submit', delayed_submit)
        opening = asyncio.create_task(asyncio.to_thread(peer.robot.display.open_stream, timeout=2))
        closing = None
        try:
            assert await asyncio.to_thread(reached.wait, 1)
            if retire == 'offline':
                await peer.offline()
            else:
                closing = asyncio.create_task(asyncio.to_thread(peer.robot.display._close, timeout=2))
                async def closed():
                    while not peer.robot.display._closed:
                        await asyncio.sleep(0.001)
                await asyncio.wait_for(closed(), 1)
        finally:
            release_thread.set()
            release_loop.set()
        result = (await asyncio.wait_for(asyncio.gather(opening, return_exceptions=True), 3))[0]
        if closing is not None:
            await asyncio.wait_for(closing, 3)
        assert not peer.commands, 'retired opening reached the new device channel'
        assert isinstance(result, WatcheRobotError)
        assert not peer.transport._pending
        assert not peer.robot.display._opening
        assert peer.robot.display._session is None
        if retire == 'offline':
            monkeypatch.setattr(peer.transport, '_submit', submit)
            current = await asyncio.to_thread(peer.robot.display.open_stream)
            assert await asyncio.to_thread(current.present, JPEG) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['managed', 'desktop'])
def test_queued_presentation_retires_before_worker_can_cancel(monkeypatch, kind):
    async def scenario():
        peer = MemoryPeer(kind, monkeypatch)
        old = await asyncio.to_thread(peer.robot.display.open_stream)
        reached = threading.Event()
        cancel_reached = threading.Event()
        release_cancel = threading.Event()
        release_loop = asyncio.Event()
        send_finished = asyncio.Event()
        submit = peer.transport._submit

        def delayed_submit(coroutine):
            local = coroutine.cr_frame.f_locals
            frame = local.get('frame')
            if frame is None and local.get('args'):
                frame = local['args'][-1]
            if not isinstance(frame, bytes):
                return submit(coroutine)

            async def deferred():
                try:
                    reached.set()
                    await release_loop.wait()
                    return await coroutine
                finally:
                    coroutine.close()
                    send_finished.set()
            future = submit(deferred())
            cancel = future.cancel

            def delayed_cancel():
                # Only delay the worker at the cancellation boundary. The real
                # Future and real cancellation still run when it is scheduled.
                cancel_reached.set()
                assert release_cancel.wait(3)
                return cancel()
            monkeypatch.setattr(future, 'cancel', delayed_cancel)
            return future

        monkeypatch.setattr(peer.transport, '_submit', delayed_submit)
        presenting = asyncio.create_task(asyncio.to_thread(old.present, JPEG, timeout=2))
        try:
            assert await asyncio.to_thread(reached.wait, 1)
            await peer.offline()
            assert await asyncio.to_thread(cancel_reached.wait, 1)
            current = await asyncio.to_thread(peer.robot.display.open_stream)
            release_loop.set()
            await asyncio.wait_for(send_finished.wait(), 1)
            assert not peer.images, 'queued old image reached the replacement stream'
            assert peer.robot.display._session is current
        finally:
            release_loop.set()
            release_cancel.set()
            result = (await asyncio.wait_for(asyncio.gather(presenting, return_exceptions=True), 3))[0]
        assert isinstance(result, WatcheRobotError)
        monkeypatch.setattr(peer.transport, '_submit', submit)
        assert await asyncio.to_thread(current.present, JPEG) == 1
    asyncio.run(scenario())
