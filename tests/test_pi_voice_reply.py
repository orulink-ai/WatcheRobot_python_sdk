"""The Pi tool receives playback completion before doing any device work."""
import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'examples' / 'joyinside_pi'


def agent_module():
    spec = importlib.util.spec_from_file_location('pi_voice_reply_agent', ROOT / 'agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_pi(tmp_path, *, title='watcherobot.voice_reply.v1', text='好的，我帮你打开小红书。'):
    path = tmp_path / 'pi.py'
    marker = tmp_path / 'tool-started'
    path.write_text('''import json,sys
from pathlib import Path
for line in sys.stdin:
 c=json.loads(line)
 if c['type']=='prompt':
  print(json.dumps({'type':'response','id':c['id'],'success':True}),flush=True)
  print(json.dumps({'type':'extension_ui_request','method':'confirm','title':TITLE,'message':TEXT,'id':'voice-1'}),flush=True)
 elif c['type']=='extension_ui_response':
  if c['id']=='voice-1' and c.get('confirmed') is True:
   Path(MARKER).write_text('executed')
   print(json.dumps({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'已经打开了。'}]}}),flush=True)
   print(json.dumps({'type':'agent_settled'}),flush=True)
'''.replace('TITLE', repr(title)).replace('TEXT', repr(text)).replace('MARKER', repr(str(marker))))
    return [sys.executable, str(path)], marker


def test_voice_tool_waits_for_playback_then_keeps_final_separate(tmp_path):
    async def check():
        argv, marker = fake_pi(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        spoken = []
        async def speak(text):
            spoken.append(text);entered.set();await release.wait()
        async with agent_module().PiAgent(argv) as pi:
            asking = asyncio.create_task(pi.ask('打开小红书', on_speak=speak, timeout=2))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                assert not marker.exists() and not asking.done()
                release.set()
                assert await asking == '已经打开了。'
                assert marker.exists() and spoken == ['好的，我帮你打开小红书。']
            finally:
                release.set();asking.cancel();await asyncio.gather(asking, return_exceptions=True)
    asyncio.run(check())


@pytest.mark.parametrize('mode', ['failure', 'cancel'])
def test_failed_or_cancelled_playback_never_releases_tool(tmp_path, mode):
    async def check():
        argv, marker = fake_pi(tmp_path)
        entered = asyncio.Event()
        async def speak(text):
            entered.set()
            if mode == 'failure': raise RuntimeError('playback failed')
            await asyncio.Event().wait()
        async with agent_module().PiAgent(argv) as pi:
            asking = asyncio.create_task(pi.ask('打开小红书', on_speak=speak, timeout=2))
            await asyncio.wait_for(entered.wait(), 1)
            if mode == 'cancel': asking.cancel()
            result = (await asyncio.gather(asking, return_exceptions=True))[0]
            assert isinstance(result, (RuntimeError, asyncio.CancelledError))
            assert not marker.exists() and pi.process.returncode is not None
    asyncio.run(check())


@pytest.mark.parametrize('title,text', [('ordinary permission', 'Allow?'), ('watcherobot.voice_reply.v1',''),
    ('watcherobot.voice_reply.v1','a'*81)])
def test_unrecognized_or_invalid_request_never_calls_speech_or_releases_tool(tmp_path, title, text):
    async def check():
        argv, marker = fake_pi(tmp_path, title=title, text=text)
        spoken=[]
        async def speak(text): spoken.append(text)
        async with agent_module().PiAgent(argv) as pi:
            with pytest.raises(RuntimeError): await pi.ask('test', on_speak=speak, timeout=2)
            assert spoken == [] and not marker.exists()
    asyncio.run(check())


def test_voice_request_without_playback_owner_fails_closed(tmp_path):
    async def check():
        argv, marker = fake_pi(tmp_path)
        async with agent_module().PiAgent(argv) as pi:
            with pytest.raises(RuntimeError): await pi.ask('test', timeout=2)
            assert not marker.exists()
    asyncio.run(check())


def test_configured_pi_command_always_loads_voice_ordering_extension(monkeypatch):
    monkeypatch.setenv('PI_COMMAND_JSON', '["node", "pi-entry.js", "--mode", "rpc"]')
    argv = agent_module().pi_command()
    assert argv == ['node', 'pi-entry.js', '--mode', 'rpc', '--extension', str(ROOT / 'voice_extension.mjs')]
    assert Path(argv[-1]).is_file()
