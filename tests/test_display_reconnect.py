"""Real Robot/transport admission regressions, with only the socket in memory."""
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


@pytest.mark.parametrize('delay', ['before_submit', 'after_submit'])
@pytest.mark.parametrize('kind', ['managed', 'desktop'])
def test_old_close_cannot_close_reused_stream_after_observed_disconnect(monkeypatch, delay, kind):
    async def scenario():
        transport = (DaemonApplicationTransport(command_timeout=1) if kind == 'managed'
            else DesktopRobotSession('ws://unused.invalid', command_timeout=1))
        transport._loop = asyncio.get_running_loop()
        transport.capabilities = ('display.stream.v1',)
        robot = WatcheRobot._from_transport(transport)
        state = {'generation': 0, 'active': None}
        sent = []

        async def socket_send(*args):
            frame = args[-1]
            if isinstance(frame, bytes):
                stream_id, sequence = struct.unpack_from('<HI', frame, 6)
                assert state['active'] == (state['generation'], stream_id)
                robot._on_message({'type': 'evt.display.presented', 'data': {
                    'stream_id': stream_id, 'sequence': sequence, 'device_sequence': 3, 'code': 0}})
                return
            command = json.loads(frame)
            kind, data = command['type'], command['data']
            sent.append((state['generation'], kind))
            if kind == 'ctrl.display.open':
                state['active'] = (state['generation'], 41)
                ack = {'stream_id': 41, 'max_frame_bytes': 65536}
            else:
                assert kind == 'ctrl.display.close'
                state['active'] = None
                ack = {}
            transport._pending[data['command_id']].set_result({'type': 'sys.ack', 'data': ack})

        monkeypatch.setattr(transport, '_send', socket_send)
        old = await asyncio.to_thread(robot.display.open_stream)
        reached = threading.Event()
        release_thread = threading.Event()
        release_loop = asyncio.Event()
        submit = transport._submit

        def delayed_submit(coroutine):
            if coroutine.cr_frame.f_locals.get('message_type') != 'ctrl.display.close':
                return submit(coroutine)
            if delay == 'before_submit':
                reached.set()
                assert release_thread.wait(2)
                return submit(coroutine)

            async def deferred():
                try:
                    reached.set()
                    await release_loop.wait()
                    return await coroutine
                finally:
                    coroutine.close()
            return submit(deferred())

        monkeypatch.setattr(transport, '_submit', delayed_submit)
        closing = asyncio.create_task(asyncio.to_thread(old.close, timeout=1.5))
        try:
            assert await asyncio.to_thread(reached.wait, 1)
            offline = {'type': 'daemon.device.state', 'data': {'online': False}}
            if kind == 'managed':
                await transport._on_frame(ApplicationChannel.DEVICE, json.dumps(offline))
            else:
                robot._on_message(offline)
            state['generation'] = 1
            current = await asyncio.to_thread(robot.display.open_stream)
            assert state['active'] == (1, 41)
        finally:
            release_thread.set()
            release_loop.set()
        result = (await asyncio.wait_for(asyncio.gather(closing, return_exceptions=True), 2))[0]
        assert state['active'] == (1, 41), 'old close reached the new firmware stream'
        assert (1, 'ctrl.display.close') not in sent
        assert isinstance(result, WatcheRobotError)
        assert robot.display._session is current
        assert not current._closed
        assert not transport._pending
        assert await asyncio.to_thread(current.present, b'\xff\xd8content\xff\xd9') == 1
    asyncio.run(scenario())
