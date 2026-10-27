"""Speculative text synthesis must never commit intermediate Pi speech."""
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import logging

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'examples' / 'joyinside_pi'


def load(name):
    sys.path.insert(0, str(ROOT))
    try:
        spec = importlib.util.spec_from_file_location('stream_test_' + name, ROOT / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def delta(text, index=0):
    return {'type': 'message_update', 'assistantMessageEvent': {
        'type': 'text_delta', 'contentIndex': index, 'delta': text}}


def end(text, **kwargs):
    return {'type': 'message_end', 'message': {
        'role': 'assistant', 'content': [{'type': 'text', 'text': text}], **kwargs}}


async def wait_for(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(.005)
    raise AssertionError('Expected asynchronous progress')


class Speech:
    def __init__(self):
        self.prepared = []
        self.played = []
        self.streams = 0

    async def say_segments(self, segments, *, before_play):
        from tts_pipeline import run_tts_pipeline
        async def synth(text, number):
            self.prepared.append(text)
            return text.encode() * 2
        async def play(pcm, number):
            raise AssertionError('Must use one continuous stream')
        async def stream(frames):
            self.streams += 1
            self.played.extend([pcm async for pcm in frames])
        await run_tts_pipeline(segments, synthesize=synth, play_segment=play,
                               play_stream=stream, before_play=before_play)


def test_short_sentence_synthesizes_before_settled_but_never_plays_early(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        preview = load('streaming_text').TextSpeechPreview(speech.say_segments)
        try:
            await preview.handle(delta('第一句话。第'))
            await wait_for(lambda: speech.prepared == ['第一句话。'])
            assert speech.played == [] and speech.streams == 0
            await preview.handle(delta('二句话没有标点'))
            text = '第一句话。第二句话没有标点'
            await preview.handle(end(text, stopReason='stop'))
            assert await preview.play(text)
            assert speech.prepared == ['第一句话。', '第二句话没有标点']
            assert speech.played == [part.encode() * 2 for part in speech.prepared]
            assert speech.streams == 1
        finally:
            await preview.close()
    asyncio.run(run())


@pytest.mark.parametrize('invalidate', [
    {'type': 'auto_retry_start'},
    {'type': 'message_update', 'assistantMessageEvent': {'type': 'toolcall_start', 'contentIndex': 1}},
    {'type': 'extension_ui_request', 'method': 'confirm', 'title': 'watcherobot.voice_reply.v1'},
    {'type': 'message_start', 'message': {'role': 'assistant'}},
])
def test_intermediate_speech_is_discarded_on_retry_tool_or_next_message(monkeypatch, invalidate):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        preview = load('streaming_text').TextSpeechPreview(speech.say_segments)
        try:
            await preview.handle(delta('这是中间内容。下'))
            await wait_for(lambda: bool(speech.prepared))
            await preview.handle(invalidate)
            assert speech.played == []
            await preview.handle({'type': 'message_start', 'message': {'role': 'assistant'}})
            await preview.handle(delta('最终结果。'))
            await preview.handle(end('最终结果。', stopReason='stop'))
            assert await preview.play('最终结果。')
            assert speech.played == ['最终结果。'.encode() * 2]
        finally:
            await preview.close()
    asyncio.run(run())


@pytest.mark.parametrize('where', ['text_end', 'message_end'])
def test_authoritative_replacement_never_plays_old_text(monkeypatch, where):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        preview = load('streaming_text').TextSpeechPreview(speech.say_segments)
        try:
            await preview.handle(delta('旧内容。后'))
            await wait_for(lambda: bool(speech.prepared))
            if where == 'text_end':
                await preview.handle({'type': 'message_update', 'assistantMessageEvent': {
                    'type': 'text_end', 'contentIndex': 0, 'content': '正确内容。'}})
            await preview.handle(end('正确内容。', stopReason='stop'))
            assert await preview.play('正确内容。')
            assert speech.played == ['正确内容。'.encode() * 2]
        finally:
            await preview.close()
    asyncio.run(run())


def test_thinking_tool_arguments_and_tool_call_messages_are_not_speech(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        preview = load('streaming_text').TextSpeechPreview(speech.say_segments)
        try:
            for kind in ['thinking_delta', 'toolcall_delta']:
                await preview.handle({'type': 'message_update', 'assistantMessageEvent': {
                    'type': kind, 'contentIndex': 0, 'delta': 'private thoughts and arguments。'}})
            await preview.handle(end('将执行操作。', stopReason='toolUse'))
            assert not await preview.play('将执行操作。')
            assert speech.prepared == [] and speech.played == []
        finally:
            await preview.close()
    asyncio.run(run())


def test_full_text_queue_falls_back_without_blocking_rpc(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        preview = load('streaming_text').TextSpeechPreview(speech.say_segments, max_pending=2)
        try:
            await asyncio.wait_for(preview.handle(delta('一。二。三。四。五。')), .5)
            await asyncio.wait_for(preview.handle(end('一。二。三。四。五。', stopReason='stop')), .5)
            assert not await preview.play('一。二。三。四。五。')
            assert speech.played == []
        finally:
            await preview.close()
    asyncio.run(run())


def test_cancelled_preview_cannot_play_late_audio(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        preview = load('streaming_text').TextSpeechPreview(speech.say_segments)
        await preview.handle(delta('即将取消。下'))
        await wait_for(lambda: bool(speech.prepared))
        await preview.close()
        assert speech.played == [] and not await preview.play('即将取消。下')
    asyncio.run(run())


def test_retry_does_not_block_rpc_on_slow_cancel_cleanup(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        entered, release, ended = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def slow_speech(segments, *, before_play):
            try:
                entered.set()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            finally:
                ended.set()
        preview = load('streaming_text').TextSpeechPreview(slow_speech)
        try:
            await preview.handle(delta('一句话。后'))
            await entered.wait()
            await asyncio.wait_for(preview.handle({'type': 'auto_retry_start'}), .1)
            assert not ended.is_set(), 'RPC invalidates immediately without waiting for cloud cleanup'
        finally:
            release.set()
            await preview.close()
            assert ended.is_set()
    asyncio.run(run())


def test_url_expanding_across_delta_replaces_old_chunk_boundaries(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        preview = load('streaming_text').TextSpeechPreview(speech.say_segments)
        text = '你' * 24 + 'http://example.com'
        try:
            await preview.handle(delta('你' * 24 + 'http:/'))
            await wait_for(lambda: bool(speech.prepared))
            await preview.handle(delta('/example.com'))
            await preview.handle(end(text, stopReason='stop'))
            assert await preview.play(text)
            expected = [part.encode() * 2 for part in load('agent').speech_chunks(text)]
            assert speech.played == expected, 'Changed URL boundaries must not duplicate the old prefix'
        finally:
            await preview.close()
    asyncio.run(run())


def test_multiple_text_blocks_follow_authoritative_order(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        preview = load('streaming_text').TextSpeechPreview(speech.say_segments)
        try:
            await preview.handle(delta('第二句话。尾', index=2))
            await wait_for(lambda: bool(speech.prepared))
            await preview.handle(delta('第一句话。', index=0))
            message = end('')
            message['message']['content'] = [
                {'type': 'text', 'text': '第一句话。'}, {'type': 'thinking', 'thinking': 'secret'},
                {'type': 'text', 'text': '第二句话。尾'}]
            await preview.handle(message)
            assert await preview.play('第一句话。第二句话。尾')
            assert speech.played == [part.encode() * 2 for part in ['第一句话。', '第二句话。', '尾']]
        finally:
            await preview.close()
    asyncio.run(run())


def test_preview_failure_or_text_budget_overflow_uses_normal_tts_fallback(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    async def run():
        speech = Speech()
        async def failing(segments, *, before_play):
            raise RuntimeError('cloud failed before commit')
        for say, budget in [(failing, 16384), (speech.say_segments, 2)]:
            preview = load('streaming_text').TextSpeechPreview(say, max_text=budget)
            try:
                await preview.handle(delta('这是一句话。后'))
                await asyncio.sleep(.01)
                await preview.handle(end('这是一句话。后'))
                assert not await preview.play('这是一句话。后')
            finally:
                await preview.close()
        assert speech.played == []
    asyncio.run(run())


def test_pi_rpc_delivers_text_events_before_final_reply(tmp_path):
    marker = tmp_path / 'delta-received'
    script = tmp_path / 'pi.py'
    script.write_text('''import json,sys,time
from pathlib import Path
for line in sys.stdin:
 c=json.loads(line)
 if c['type']=='prompt':
  print(json.dumps({'type':'response','id':c['id'],'success':True}),flush=True)
  print(json.dumps({'type':'message_update','assistantMessageEvent':{'type':'text_delta','contentIndex':0,'delta':'hello.'}}),flush=True)
  while not Path(MARKER).exists(): time.sleep(.005)
  print(json.dumps({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'hello.'}]}}),flush=True)
  print(json.dumps({'type':'agent_settled'}),flush=True)
'''.replace('MARKER', repr(str(marker))))
    async def run():
        received = []
        async def event(record):
            received.append(record['type'])
            if record['type'] == 'message_update':
                marker.write_text('ok')
        async with load('agent').PiAgent([sys.executable, str(script)]) as pi:
            assert await pi.ask('hello', on_event=event, timeout=2) == 'hello.'
        assert 'message_update' in received and 'message_end' in received
    asyncio.run(run())


def test_close_joins_live_rpc_reader_before_draining_stdout(tmp_path):
    script = tmp_path / 'pi.py'
    script.write_text('''import json,sys
for line in sys.stdin:
 c=json.loads(line)
 if c['type']=='prompt':
  print(json.dumps({'type':'agent_start'}),flush=True)
 elif c['type']=='abort': break
''')
    async def run():
        entered = asyncio.Event()
        async def event(record): entered.set()
        pi = await load('agent').PiAgent([sys.executable, str(script)]).__aenter__()
        asking = asyncio.create_task(pi.ask('hello', on_event=event, timeout=3))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.gather(pi.close(), pi.close())
            result = (await asyncio.gather(asking, return_exceptions=True))[0]
            assert isinstance(result, (asyncio.CancelledError, RuntimeError))
            assert pi.process.returncode is not None
        finally:
            asking.cancel()
            await asyncio.gather(asking, return_exceptions=True)
            await pi.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['success', 'pause', 'tool'])
def test_service_overlaps_pi_and_tts_without_bypassing_preface(monkeypatch, mode):
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setenv('WATCHER_SPEECH_ONCE', '1')
    async def run():
        from watcherobot.voice.contracts import AudioChunk
        from speech_backends import DoubaoSession
        prepared, pi_release = asyncio.Event(), asyncio.Event()
        events = []
        class Device:
            supports_continuous_playback = True
            async def stop(self): pass
            async def play(self, pcm): events.append('preface-play')
            async def play_stream(self, frames):
                events.append('stream-start')
                async for pcm in frames: events.append('audio')
        class TTS:
            async def synthesize(self, text):
                events.append(('synth', text))
                prepared.set()
                yield AudioChunk(b'xx')
            async def close(self): pass
        class Session(DoubaoSession):
            async def recognize(self, timeout): return '你好'
        session = Session(Device(), None, TTS(), 1, 1)
        class Backend:
            async def open(self): return session
            async def close(self): pass
            def snapshot(self): return {'id': 'doubao', 'configured': True}
        class Agent:
            async def __aenter__(self): return self
            async def close(self): pass
            async def ask(self, text, *, on_speak, on_event=None):
                assert on_event is not None, 'Service must connect Pi deltas to TTS'
                await on_event(delta('你好。下'))
                await prepared.wait()
                assert 'stream-start' not in events
                if mode == 'tool':
                    await on_event({'type': 'extension_ui_request', 'method': 'confirm'})
                    await on_speak('好的，我帮你查看。')
                    assert 'preface-play' in events
                    events.append('tool')
                    await on_event({'type': 'message_start', 'message': {'role': 'assistant'}})
                await pi_release.wait()
                await on_event(end('最终结果。', stopReason='stop'))
                events.append('pi-settled')
                return '最终结果。'
        behavior = SimpleNamespace(snapshot=lambda: {'active': False})
        async def noop(*args, **kwargs): pass
        behavior.start = behavior.set_phase = behavior.stop = noop
        behavior.user_cue = lambda text: None
        robot = SimpleNamespace(device_info={}, refresh_device_info=lambda **kw: {})
        service = load('service').ConversationService(
            SimpleNamespace(robot=robot, shutdown_requested=False, logger=logging.getLogger('test')),
            agent_factory=Agent, speech_backends={'doubao': Backend()}, backend_name='doubao', behavior=behavior)
        runner = asyncio.create_task(service.run())
        try:
            service.start()
            await wait_for(lambda: prepared.is_set() or bool(service.error))
            assert not service.error, service.error
            if mode == 'pause':
                service.pause()
                await wait_for(lambda: session.closed)
                assert 'stream-start' not in events and 'audio' not in events
            else:
                pi_release.set()
                await wait_for(lambda: service.turns == 1 or bool(service.error))
                assert not service.error, service.error
                assert events.index('pi-settled') < events.index('stream-start')
                assert events.count('stream-start') == 1
                if mode == 'tool':
                    assert events.index('preface-play') < events.index('tool') < events.index('stream-start')
        finally:
            pi_release.set()
            service.shutdown()
            await runner
    asyncio.run(run())
