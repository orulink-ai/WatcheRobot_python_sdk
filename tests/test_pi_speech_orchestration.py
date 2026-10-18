"""Behavior and prewarm integrate without bypassing the Application channel."""
import asyncio
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / 'examples' / 'joyinside_pi'


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    spec = importlib.util.spec_from_file_location('pi_orchestration', ROOT / 'service.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Behavior:
    def __init__(self):
        self.active = False
        self.cleanup_pending = False
        self.events = []
        self.fail_cleanup = False
    def snapshot(self):
        return {'active': self.active, 'cleanup_pending': self.cleanup_pending}
    async def start(self):
        self.active = True
        self.events.append('start')
    def user_cue(self, text): self.events.append(('cue', text))
    async def set_phase(self, phase, text=''): self.events.append((phase, text))
    async def stop(self):
        if not self.active and not self.cleanup_pending: return
        self.cleanup_pending = True
        self.events.append('stop')
        if self.fail_cleanup: raise RuntimeError('unconfirmed motion stop')
        self.active = False
        self.cleanup_pending = False


async def until(predicate):
    for _ in range(150):
        if predicate(): return
        await asyncio.sleep(.02)
    raise AssertionError('condition not reached')


def test_prewarm_starts_during_capture_and_behavior_spans_whole_conversation(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setenv('WATCHER_SPEECH_ONCE', '1')
    async def check():
        events, behavior = [], Behavior()
        class Agent:
            async def __aenter__(self):
                events.append('pi-start')
                return self
            async def ask(self, text, *, on_speak):
                assert 'recognized' in text
                events.append('pi-ask')
                return '第一句。第二句。'
            async def close(self): events.append('pi-close')
        class Session:
            async def recognize(self, timeout):
                await until(lambda: 'pi-start' in events)
                events.append('recognized')
                return 'recognized'
            async def say(self, text): events.append('ack-complete')
            async def say_segments(self, segments):
                assert list(segments) == ['第一句。', '第二句。']
                events.append('pipeline')
            async def close(self): events.append('audio-close')
            async def cancel(self): pass
        session = Session()
        class Backend:
            async def open(self): return session
            async def close(self): pass
            def snapshot(self): return {'id':'doubao','configured':True}
        robot = SimpleNamespace(device_info={'device_id':'robot'}, refresh_device_info=lambda **kw: {})
        service = module.ConversationService(SimpleNamespace(robot=robot, shutdown_requested=False,
            logger=logging.getLogger('test')), agent_factory=Agent, speech_backends={'doubao': Backend()},
            backend_name='doubao', behavior=behavior)
        runner = asyncio.create_task(service.run())
        try:
            service.start()
            await until(lambda: service.turns == 1 and service.phase == 'paused')
            assert events.index('pi-start') < events.index('recognized') < events.index('pi-ask')
            assert events.index('pipeline') < events.index('audio-close')
            assert events.count('ack-complete') == 0, 'Ordinary chat must not have an extra acknowledgement'
            assert behavior.events == ['start', ('listening',''), ('cue','recognized'),
                ('thinking',''), ('speaking','第一句。第二句。'), 'stop']
            assert not behavior.active
            assert set(service.snapshot()['timings_ms']) >= {'recognition','pi','speech'}
        finally:
            service.shutdown()
            await runner
        assert events.count('pi-start') == 1
        assert events.count('pi-close') == 1
    asyncio.run(check())


def test_unconfirmed_behavior_cleanup_blocks_new_start_and_backend_switch(monkeypatch):
    module = load(monkeypatch)
    behavior = Behavior()
    behavior.cleanup_pending = True
    service = module.ConversationService(SimpleNamespace(robot=SimpleNamespace(device_info={})), behavior=behavior)
    import pytest
    with pytest.raises(module.ConnectionError): service.start()
    with pytest.raises(module.ConnectionError): service.select_backend('joyinside')
    assert not service.snapshot()['speech_switch_available']


async def acknowledgement_case(module, mode):
    events, behavior = [], Behavior()
    started, release = asyncio.Event(), asyncio.Event()
    class Agent:
        prompts = []
        async def __aenter__(self): return self
        async def ask(self, text, *, on_speak):
            self.prompts.append(text)
            events.append('ask')
            await on_speak('好的，我帮你打开小红书。')
            assert 'ack-complete' in events
            events.append('tool')
            return '已经处理完了。'
        async def close(self): pass
    class Session:
        cancelled = False
        closed = False
        async def recognize(self, timeout):
            events.append('capture-closed')
            return '打开小红书'
        async def acknowledge(self, text):
            assert events == ['capture-closed','ask']
            assert text == '好的，我帮你打开小红书。'
            events.append('ack-start');started.set()
            await release.wait()
            if self.cancelled: raise asyncio.CancelledError
            if mode == 'failure': raise RuntimeError('ack failed')
            events.append('ack-complete')
        async def say_segments(self, segments):
            events.append('reply')
        async def cancel(self):
            self.cancelled = True;release.set()
        async def close(self): self.closed = True
    session, agent = Session(), Agent()
    class Backend:
        async def open(self): return session
        async def close(self): pass
        def snapshot(self): return {'id':'doubao','configured':True}
    robot = SimpleNamespace(device_info={'device_id':'robot'}, refresh_device_info=lambda **kw: {})
    service = module.ConversationService(SimpleNamespace(robot=robot, shutdown_requested=False,
        logger=logging.getLogger('test')), agent_factory=lambda:agent,
        speech_backends={'doubao':Backend()}, backend_name='doubao', behavior=behavior)
    runner = asyncio.create_task(service.run())
    try:
        service.start()
        await until(started.is_set)
        assert service.phase == 'acknowledging'
        assert len(agent.prompts) == 1
        assert 'tool' not in events, 'Pi may reason, but tools must wait for physical playback'
        if mode == 'pause': service.pause()
        else: release.set()
        if mode == 'success':
            await until(lambda:service.turns == 1 and session.closed)
            assert events == ['capture-closed','ask','ack-start','ack-complete','tool','reply']
            messages = service.snapshot()['messages']
            assert messages[1]['kind'] == 'preface'
            assert messages[1]['text'] == '好的，我帮你打开小红书。'
            assert messages[-1]['text'] == '已经处理完了。'
            assert 'acknowledgement' in service.snapshot()['timings_ms']
        elif mode == 'pause':
            await until(lambda:session.closed and service.phase=='paused')
            assert len(agent.prompts) == 1 and 'tool' not in events and 'reply' not in events
            assert service.error == ''
        else:
            await until(lambda:session.closed and bool(service.error))
            assert len(agent.prompts) == 1 and 'tool' not in events and 'reply' not in events
            assert 'ack failed' in service.error
    finally:
        release.set();service.shutdown();await runner


def test_received_acknowledgement_is_played_before_pi_operations(monkeypatch):
    module = load(monkeypatch)
    monkeypatch.setenv('WATCHER_SPEECH_ONCE','1')
    asyncio.run(acknowledgement_case(module,'success'))


def test_pause_during_acknowledgement_never_starts_pi_operations(monkeypatch):
    asyncio.run(acknowledgement_case(load(monkeypatch),'pause'))


def test_failed_acknowledgement_never_starts_pi_operations(monkeypatch):
    asyncio.run(acknowledgement_case(load(monkeypatch),'failure'))
