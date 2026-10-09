import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_agent import CodexAgent


def test_realtime_feature_is_enabled_for_child_only():
    from codex_agent import app_server_command
    command = app_server_command()
    assert '--enable' in command
    assert command[command.index('--enable') + 1] == 'realtime_conversation'
    assert command[-3:] == ['app-server', '--listen', 'stdio://']


def test_rpc_reads_responses_and_rejects_server_commands():
    async def run():
        agent = CodexAgent()
        responses = []
        async def send(payload, **kwargs):
            responses.append(payload)
        agent.send = send
        await agent.dispatch({"id": 77, "method": "item/commandExecution/requestApproval", "params": {}})
        assert responses[-1]["id"] == 77
        assert "error" in responses[-1]
        future = asyncio.get_running_loop().create_future()
        agent.pending[1] = future
        await agent.dispatch({"id": 1, "result": {"ok": True}})
        assert await future == {"ok": True}
    asyncio.run(run())


def test_close_fails_pending_requests():
    async def run():
        agent = CodexAgent()
        future = asyncio.get_running_loop().create_future()
        agent.pending[1] = future
        await agent.close()
        with pytest.raises(ConnectionError):
            await future
        assert not agent.pending
    asyncio.run(run())


@pytest.mark.parametrize('method', ['thread/realtime/error', 'thread/realtime/closed', 'local/error'])
def test_startup_failure_rejects_ready_before_microphone_can_open(method):
    async def run():
        agent = CodexAgent()
        agent.ready = asyncio.get_running_loop().create_future()
        await agent.dispatch({'method': method, 'params': {'message': 'auth failed'}})
        with pytest.raises(RuntimeError, match='auth failed'):
            await agent.ready
    asyncio.run(run())


def test_started_notification_resolves_ready():
    async def run():
        agent = CodexAgent()
        agent.ready = asyncio.get_running_loop().create_future()
        await agent.dispatch({'method': 'thread/realtime/started', 'params': {}})
        assert await agent.ready is None
    asyncio.run(run())


def test_windows_npm_uses_native_binary_so_close_owns_the_process(tmp_path, monkeypatch):
    import codex_agent
    executable = tmp_path / 'codex.cmd'
    native = tmp_path / 'node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe'
    native.parent.mkdir(parents=True)
    native.touch()
    monkeypatch.delenv('WATCHER_CODEX_BINARY', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(codex_agent.shutil, 'which', lambda _: str(executable))
    monkeypatch.setattr(codex_agent.platform, 'machine', lambda: 'AMD64')
    assert codex_agent.codex_command() == [str(native)]


def test_desktop_native_binary_preferred_over_outdated_npm(tmp_path, monkeypatch):
    import codex_agent
    native = tmp_path / 'OpenAI/Codex/bin/installed-release/codex.exe'
    native.parent.mkdir(parents=True)
    native.touch()
    monkeypatch.delenv('WATCHER_CODEX_BINARY', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setattr(codex_agent.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(codex_agent.shutil, 'which', lambda _: 'outdated/codex.cmd')
    assert codex_agent.codex_command() == [str(native)]


def test_explicit_binary_override_has_priority(monkeypatch):
    import codex_agent
    monkeypatch.setenv('WATCHER_CODEX_BINARY', '/explicit/codex')
    assert codex_agent.codex_command() == ['/explicit/codex']


def test_dynamic_tool_dispatch_is_nonblocking_and_duplicate_call_is_not_reexecuted():
    async def run():
        agent = CodexAgent()
        agent.thread_id = 't'
        agent.active_turn = 'turn'
        replies, calls = [], []
        release = asyncio.Event()
        async def handler(name, arguments, **metadata):
            calls.append((name, arguments))
            await release.wait()
            return {'success': True, 'contentItems': [{'type': 'inputText', 'text': 'photo'}]}
        async def send(payload, **kwargs):
            replies.append(payload)
        agent.send, agent.tool_handler = send, handler
        payload = {'id': 99, 'method': 'item/tool/call', 'params': {
            'threadId': 't', 'turnId': 'turn', 'callId': 'c', 'tool': 'observe_scene', 'arguments': {'scope': 'current'}}}
        await asyncio.wait_for(agent.dispatch(payload), .1)
        await agent.dispatch({**payload, 'id': 100})
        await asyncio.sleep(.01)
        assert len(calls) == 1
        release.set()
        await asyncio.sleep(.02)
        assert {r['id'] for r in replies} == {99, 100}
        await agent.dispatch({**payload, 'id': 101})
        await asyncio.sleep(.01)
        assert len(calls) == 1 and not replies[-1]['result']['success']
        await agent.close()
    asyncio.run(run())


def test_cancelled_turn_cannot_execute_late_tools_or_publish_completion():
    async def run():
        agent = CodexAgent()
        agent.thread_id, agent.active_turn = 't', 'old'
        calls, replies = [], []
        async def handler(*args, **kwargs):
            calls.append(args)
        async def send(payload, **kwargs):
            replies.append(payload)
        agent.tool_handler, agent.send = handler, send
        agent.invalidate_turn()
        await agent.dispatch({'id': 1, 'method': 'item/tool/call', 'params': {
            'threadId': 't', 'turnId': 'old', 'callId': 'late', 'tool': 'aim_camera', 'arguments': {'position': 'pan80'}}})
        await agent.dispatch({'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'old', 'status': 'completed'}}})
        await asyncio.sleep(.01)
        assert not calls and not replies[-1]['result']['success']
        assert agent.events.empty()
    asyncio.run(run())


def test_full_event_queue_still_delivers_fatal_error():
    async def run():
        agent = CodexAgent()
        for _ in range(agent.events.maxsize):
            agent.events.put_nowait({'method': 'noise'})
        agent.fail('reader exited')
        assert (await agent.events.get())['method'] == 'local/error'
    asyncio.run(run())


def test_photo_validation_occurs_inside_actual_stdio_write_lock():
    from types import SimpleNamespace
    import json
    async def run():
        agent = CodexAgent()
        written = []
        class Pipe:
            def write(self, data):
                written.append(json.loads(data))
            async def drain(self):
                pass
        agent.process = SimpleNamespace(stdin=Pipe())
        allowed = True
        agent.tool_result_validator = lambda result: result if allowed else agent._denied('已撤权')
        await agent.write_lock.acquire()
        operation = asyncio.create_task(asyncio.sleep(0, result={'success': True, 'contentItems': [{'type': 'inputImage', 'imageUrl': 'old'}]}))
        reply = asyncio.create_task(agent._reply_tool(1, 'photo', operation, agent.epoch))
        await asyncio.sleep(.01)
        allowed = False
        agent.write_lock.release()
        await reply
        assert not written[0]['result']['success']
        assert not any(item['type'] == 'inputImage' for item in written[0]['result']['contentItems'])
    asyncio.run(run())


def test_speech_preparation_closes_old_rtc_before_starting_fresh_epoch():
    from types import SimpleNamespace
    async def run():
        agent = CodexAgent()
        calls = []
        async def close():
            calls.append('close')
        async def request(method, params):
            calls.append(method)
        async def start_voice():
            agent.voice_epoch += 1
            calls.append('fresh')
        agent.peer = SimpleNamespace(close=close, diagnostics=lambda: {'voiceEpoch': 0})
        agent.voice_thread_id = 'old'
        agent.request, agent._start_voice = request, start_voice
        epoch = await agent.prepare_speech()
        assert calls == ['close', 'thread/realtime/stop', 'thread/archive', 'fresh']
        assert epoch == 1
        assert agent.diagnostics()['previous'] == [{'voiceEpoch': 0}]
    asyncio.run(run())


def test_provider_is_only_explicit_or_existing_local_gateway(tmp_path, monkeypatch):
    from codex_agent import configured_provider, restricted_config
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    monkeypatch.delenv('WATCHER_CODEX_AGENT_PROVIDER', raising=False)
    assert configured_provider() is None
    (tmp_path / 'config.toml').write_text('[model_providers.opencodex]\nbase_url="http://127.0.0.1:12345/v1"\nenv_key="ROBOT_TEST_AUTH"\n[mcp_servers.example]\ncommand="example"\n', encoding='utf-8')
    monkeypatch.delenv('ROBOT_TEST_AUTH', raising=False)
    assert configured_provider() is None
    monkeypatch.setenv('ROBOT_TEST_AUTH', 'test-only')
    assert configured_provider() == 'opencodex'
    assert restricted_config()['mcp_servers.example.enabled'] is False
    assert restricted_config()['features.shell_tool'] is False
    monkeypatch.setenv('WATCHER_CODEX_AGENT_PROVIDER', 'user_selected')
    assert configured_provider() == 'user_selected'


def test_cross_thread_and_non_robot_tool_calls_remain_denied():
    async def run():
        agent = CodexAgent()
        agent.thread_id = 't'
        replies = []
        async def send(payload, **kwargs):
            replies.append(payload)
        agent.send = send
        for tool, thread in [('observe_scene', 'other'), ('exec', 't')]:
            await agent.dispatch({'id': 9, 'method': 'item/tool/call',
                                  'params': {'tool': tool, 'threadId': thread}})
            assert 'error' in replies[-1]
    asyncio.run(run())


def test_camera_images_are_attached_to_the_same_active_turn_before_tool_reply():
    from types import SimpleNamespace
    import json
    async def run():
        agent = CodexAgent()
        agent.thread_id, agent.active_turn = 'task', 'turn'
        written = []
        class Pipe:
            def write(self, data):
                payload = json.loads(data)
                written.append(payload)
                if payload.get('method') == 'turn/steer':
                    agent.pending[payload['id']].set_result({'turnId': 'turn'})
            async def drain(self):
                pass
        agent.process = SimpleNamespace(stdin=Pipe())
        result = {'success': True, 'contentItems': [
            {'type': 'inputText', 'text': '{"id":"photo","sha256":"verified-hash"}'},
            {'type': 'inputImage', 'imageUrl': 'data:image/jpeg;base64,actual'}]}
        operation = asyncio.create_task(asyncio.sleep(0, result=result))
        await agent._reply_tool(99, 'camera-call', operation, agent.epoch)
        assert written[0]['method'] == 'turn/steer'
        assert written[0]['params']['threadId'] == 'task'
        assert written[0]['params']['expectedTurnId'] == 'turn'
        assert written[0]['params']['input'][-1] == {'type': 'image', 'url': 'data:image/jpeg;base64,actual'}
        assert 'verified-hash' in written[0]['params']['input'][0]['text']
        assert written[1]['id'] == 99 and written[1]['result']['success']
        assert not any(c['type'] == 'inputImage' for c in written[1]['result']['contentItems'])
    asyncio.run(run())


def test_clear_or_cancel_while_waiting_for_attachment_lock_never_uploads_image():
    from types import SimpleNamespace
    import json
    async def scenario(cancel):
        agent = CodexAgent()
        agent.thread_id, agent.active_turn = 'task', 'turn'
        written = []
        class Pipe:
            def write(self, data):
                written.append(json.loads(data))
            async def drain(self):
                pass
        agent.process = SimpleNamespace(stdin=Pipe())
        allowed = True
        agent.tool_result_validator = lambda value: value if allowed else agent._denied('已清除')
        await agent.write_lock.acquire()
        operation = asyncio.create_task(asyncio.sleep(0, result={'success': True, 'contentItems': [
            {'type': 'inputImage', 'imageUrl': 'private-photo'}]}))
        reply = asyncio.create_task(agent._reply_tool(99, 'photo', operation, agent.epoch))
        await asyncio.sleep(.01)
        if cancel:
            agent.invalidate_turn()
        else:
            allowed = False
        agent.write_lock.release()
        await reply
        assert len(written) == 1 and written[0]['id'] == 99
        assert not written[0]['result']['success']
        assert 'private-photo' not in str(written)
    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


@pytest.mark.parametrize('invalidate', ['none', 'clear', 'permission'])
def test_nearby_result_real_validator_and_reply_tool_keep_motion_fact_and_photo_guards(invalidate):
    from contextlib import asynccontextmanager
    import io
    import json
    from PIL import Image
    from types import SimpleNamespace
    from robot_tools import RobotTools
    async def run():
        @asynccontextmanager
        async def exclusive():
            yield
        def capture(**kwargs):
            buffer = io.BytesIO()
            Image.new('RGB', (8, 8)).save(buffer, format='JPEG')
            return SimpleNamespace(data=buffer.getvalue())
        robot = SimpleNamespace(camera=SimpleNamespace(capture=capture),
            motion=SimpleNamespace(move_to=lambda **kwargs: SimpleNamespace(wait=lambda timeout: None), stop=lambda: None))
        tools = RobotTools(robot, lambda: None, exclusive, settle=0)
        tools.set_permissions({'camera': True, 'upload': True, 'motion': True})
        result = await tools.execute('observe_scene', {'scope': 'nearby'}, turn_id='turn')
        agent = CodexAgent()
        agent.thread_id, agent.active_turn = 'task', 'turn'
        agent.tool_result_validator = tools.validate_result
        written = []
        class Pipe:
            def write(self, data):
                payload = json.loads(data)
                written.append(payload)
                if payload.get('method') == 'turn/steer':
                    agent.pending[payload['id']].set_result({'turnId': 'turn'})
            async def drain(self):
                pass
        agent.process = SimpleNamespace(stdin=Pipe())
        await agent.write_lock.acquire()
        operation = asyncio.create_task(asyncio.sleep(0, result=result))
        reply = asyncio.create_task(agent._reply_tool(99, 'photo', operation, agent.epoch))
        await asyncio.sleep(.03)
        if invalidate == 'clear':
            tools.clear_photos()
        elif invalidate == 'permission':
            tools.set_permissions({'upload': False})
        agent.write_lock.release()
        await reply
        if invalidate == 'none':
            assert written[0]['method'] == 'turn/steer'
            assert sum(item['type'] == 'image' for item in written[0]['params']['input']) == 3
            assert '回中心已确认' in written[0]['params']['input'][0]['text']
            event = await agent.events.get()
            assert len(event['params']['photos']) == 3
            assert written[1]['result']['success']
        else:
            assert len(written) == 1 and not written[0]['result']['success']
            assert 'data:image' not in str(written)
    asyncio.run(run())


class CloseFaultPeer:
    def __init__(self, failure='error'):
        self.failure = failure
        self.close_calls = 0
        self.closed = False

    async def close(self):
        self.close_calls += 1
        if self.failure == 'error':
            raise RuntimeError('peer still open')
        if self.failure == 'timeout':
            await asyncio.Event().wait()
        self.closed = True


class CloseFaultProcess:
    def __init__(self, failure=None):
        self.failure = failure
        self.returncode = None
        self.terminate_calls = self.kill_calls = self.wait_calls = 0
        self.attempt_waits = 0

    def terminate(self):
        self.terminate_calls += 1
        self.attempt_waits = 0
        if self.failure == 'terminate':
            raise OSError('terminate denied')
        if self.failure == 'terminate_missing':
            raise ProcessLookupError('terminate raced process exit')

    def kill(self):
        self.kill_calls += 1
        if self.failure == 'kill':
            raise OSError('kill denied')
        if self.failure == 'kill_missing':
            raise ProcessLookupError('kill raced process exit')

    async def wait(self):
        self.wait_calls += 1
        self.attempt_waits += 1
        if self.failure in {'wait', 'terminate_missing'}:
            raise RuntimeError('process exit not observed')
        if self.failure in {'kill', 'kill_wait', 'kill_missing'}:
            if self.attempt_waits == 1:
                raise asyncio.TimeoutError('terminate did not finish')
            if self.failure in {'kill_wait', 'kill_missing'}:
                raise RuntimeError('process exit not observed')
        if self.failure == 'unconfirmed_wait':
            return 0  # A successful await alone is not proof of exit.
        self.returncode = 0
        return self.returncode


@pytest.mark.parametrize('failure', ['error', 'timeout'])
def test_close_retains_and_retries_the_same_failed_peer(failure, monkeypatch):
    import codex_agent
    original_wait_for = asyncio.wait_for

    async def bounded_wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, min(timeout, .05))

    monkeypatch.setattr(codex_agent.asyncio, 'wait_for', bounded_wait_for)

    async def run():
        agent = CodexAgent()
        peer = agent.peer = CloseFaultPeer(failure)
        for attempt in (1, 2):
            with pytest.raises(RuntimeError, match='Codex cleanup failed'):
                await agent.close()
            assert agent.peer is peer and not peer.closed
            assert peer.close_calls == attempt
        peer.failure = None
        await agent.close()
        assert peer.closed and peer.close_calls == 3 and agent.peer is None
        await agent.close()
        assert peer.close_calls == 3
    asyncio.run(run())


@pytest.mark.parametrize('failure', [
    'terminate', 'wait', 'kill', 'kill_wait', 'terminate_missing',
    'kill_missing', 'unconfirmed_wait',
])
def test_close_retains_and_retries_the_same_live_process(failure):
    async def run():
        agent = CodexAgent()
        process = agent.process = CloseFaultProcess(failure)
        for attempt in (1, 2):
            with pytest.raises(RuntimeError, match='Codex cleanup failed'):
                await agent.close()
            assert agent.process is process and process.returncode is None
            assert process.terminate_calls == attempt
        process.failure = None
        await agent.close()
        assert process.returncode == 0 and agent.process is None
        assert process.terminate_calls == 3
        await agent.close()
        assert process.terminate_calls == 3
    asyncio.run(run())


def test_close_releases_only_independently_confirmed_resources():
    async def run():
        agent = CodexAgent()
        peer = agent.peer = CloseFaultPeer()
        process = agent.process = CloseFaultProcess('terminate')
        with pytest.raises(RuntimeError, match='peer still open.*terminate denied'):
            await agent.close()
        assert agent.peer is peer and agent.process is process
        peer.failure = None
        with pytest.raises(RuntimeError, match='terminate denied'):
            await agent.close()
        assert peer.closed and agent.peer is None
        assert agent.process is process and process.returncode is None
        process.failure = None
        await agent.close()
        assert agent.process is None and process.returncode == 0
        assert peer.close_calls == 2 and process.terminate_calls == 3
    asyncio.run(run())


@pytest.mark.parametrize('missing_signal', ['terminate', 'kill'])
def test_close_confirms_process_exit_after_signal_race(missing_signal):
    class RacingProcess(CloseFaultProcess):
        def terminate(self):
            super().terminate()
            if missing_signal == 'terminate':
                raise ProcessLookupError('already exiting')

        def kill(self):
            super().kill()
            raise ProcessLookupError('already exiting')

        async def wait(self):
            if missing_signal == 'kill' and not self.kill_calls:
                raise asyncio.TimeoutError('terminate did not finish')
            return await super().wait()

    async def run():
        agent = CodexAgent()
        process = agent.process = RacingProcess()
        await agent.close()
        assert process.returncode == 0 and process.wait_calls == 1
        assert agent.process is None
    asyncio.run(run())


def test_close_releases_already_exited_process_without_signalling():
    async def run():
        agent = CodexAgent()
        process = agent.process = CloseFaultProcess('terminate')
        process.returncode = 0
        await agent.close()
        assert agent.process is None
        assert process.terminate_calls == process.kill_calls == process.wait_calls == 0
    asyncio.run(run())


@pytest.mark.parametrize('transport', ['websocket', 'unsupported', ''])
@pytest.mark.parametrize('entrypoint', ['start', '_start_voice'])
def test_unsupported_voice_transport_is_rejected_before_acquiring_resources(
        transport, entrypoint, monkeypatch):
    import codex_agent
    from unittest.mock import AsyncMock
    monkeypatch.setenv('WATCHER_CODEX_VOICE_TRANSPORT', transport)
    monkeypatch.setattr(codex_agent, 'app_server_command', lambda: ['test-only-codex'])
    monkeypatch.setattr(codex_agent, 'restricted_config', lambda: {})
    monkeypatch.setattr(codex_agent, 'configured_provider', lambda: None)
    spawn = AsyncMock(side_effect=AssertionError('must not spawn Codex'))
    monkeypatch.setattr(codex_agent.asyncio, 'create_subprocess_exec', spawn)

    async def run():
        agent = CodexAgent()
        agent.request = AsyncMock(side_effect=AssertionError('must not contact upstream'))
        with pytest.raises(ValueError, match='WebRTC|webrtc'):
            await getattr(agent, entrypoint)()
        spawn.assert_not_awaited()
        agent.request.assert_not_awaited()
        assert agent.process is None and agent.peer is None
        assert agent.ready is None and agent.voice_epoch == 0
    asyncio.run(run())


@pytest.mark.parametrize('transport', [None, 'webrtc'])
def test_default_and_explicit_webrtc_start_voice_using_only_fake_transport(
        transport, monkeypatch):
    import codex_agent
    from types import SimpleNamespace
    if transport is None:
        monkeypatch.delenv('WATCHER_CODEX_VOICE_TRANSPORT', raising=False)
    else:
        monkeypatch.setenv('WATCHER_CODEX_VOICE_TRANSPORT', transport)
    monkeypatch.setattr(codex_agent, 'restricted_config', lambda: {})
    calls = []

    class Peer:
        def __init__(self, events, *, voice_epoch, realtime=False):
            self.voice_epoch = voice_epoch

        async def offer(self):
            return 'fake-offer'

        async def answer(self, sdp):
            calls.append(('answer', sdp))

        async def close(self):
            calls.append(('close', None))

    monkeypatch.setitem(sys.modules, 'realtime_peer', SimpleNamespace(RealtimePeer=Peer))

    async def run():
        agent = CodexAgent()
        async def request(method, params):
            calls.append((method, params))
            if method == 'thread/start':
                return {'thread': {'id': 'fake-voice'}}
            assert method == 'thread/realtime/start'
            assert params['transport'] == {'type': 'webrtc', 'sdp': 'fake-offer'}
            agent.ready.set_result(None)
            agent.remote_sdp.set_result('fake-answer')
            return {}
        agent.request = request
        await agent._start_voice()
        assert agent.voice_epoch == agent.peer.voice_epoch == 1
        assert calls[-1] == ('answer', 'fake-answer')
        await agent.close()
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['speak', 'append_audio'])
def test_missing_rtc_peer_never_falls_back_to_websocket_rpc(operation):
    from unittest.mock import AsyncMock
    async def run():
        agent = CodexAgent()
        agent.voice_thread_id = 'fake-voice'
        agent.request = AsyncMock(side_effect=AssertionError('no websocket fallback'))
        argument = '测试语音' if operation == 'speak' else b'\x00\x00'
        with pytest.raises(ConnectionError, match='WebRTC|RTC'):
            await getattr(agent, operation)(argument)
        agent.request.assert_not_awaited()
    asyncio.run(run())
