"""Pi completion semantics and portable launch are tested without a provider."""
import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi"


def module():
    spec = importlib.util.spec_from_file_location("pi_speech_agent", ROOT / "agent.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_rpc_waits_for_settled_and_uses_final_text_only(tmp_path):
    fake = tmp_path / "pi.py"
    fake.write_text('''import json, sys
for line in sys.stdin.buffer:
 c=json.loads(line)
 if c['type']=='prompt':
  events=[{'type':'extension_ui_request','method':'setStatus','statusText':'working'},
   {'type':'extension_ui_request','method':'notify','message':'status'},
   {'type':'response','id':c['id'],'success':True},
   {'type':'message_update','assistantMessageEvent':{'type':'thinking_delta','delta':'secret'}},
   {'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'intermediate'}]}},
   {'type':'agent_end'},
   {'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'最终答复\\u2028第二行'}]}},
   {'type':'agent_settled'}]
  for event in events: print(json.dumps(event),flush=True)
''', encoding="utf-8")

    async def run():
        async with module().PiAgent([sys.executable, str(fake)]) as pi:
            assert await pi.ask("hello", timeout=2) == "最终答复\u2028第二行"

    asyncio.run(run())


def test_timeout_retires_process_and_cannot_reuse_stale_events(tmp_path):
    fake = tmp_path / "pi.py"
    fake.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    async def run():
        async with module().PiAgent([sys.executable, str(fake)]) as pi:
            # asyncio.TimeoutError only aliases the builtin on Python 3.11+.
            with pytest.raises(asyncio.TimeoutError):
                await pi.ask("hello", timeout=0.02)
            # A timed-out process must be reaped and must not exit normally.
            assert pi.process.returncode not in (None, 0)
            with pytest.raises(RuntimeError):
                await pi.ask("again")

    asyncio.run(run())


def test_speech_chunks_respect_utf8_and_preserve_text():
    text = "你好世界。" * 500
    chunks = list(module().speech_chunks(text))
    assert "".join(chunks) == text
    assert all(0 < len(chunk.encode("utf-8")) < 1000 for chunk in chunks)


def test_example_manifest_is_loadable_by_daemon():
    from watcherobot.runtime.daemon.application.manifest import ApplicationManifest
    assert ApplicationManifest.load(ROOT).app_id == "com.orulink.joyinside_pi"


def test_rpc_streams_only_visible_text_before_settled_without_duplicates(tmp_path):
    marker = tmp_path / "heard"
    fake = tmp_path / "stream.py"
    fake.write_text('''import json, sys, time
from pathlib import Path
for line in sys.stdin.buffer:
 c=json.loads(line)
 if c['type']=='abort': break
 if c['type']!='prompt': continue
 def emit(e): print(json.dumps(e),flush=True)
 emit({'type':'response','id':c['id'],'success':True})
 emit({'type':'message_start','message':{'role':'assistant'}})
 emit({'type':'message_update','assistantMessageEvent':{'type':'thinking_delta','delta':'secret'}})
 emit({'type':'message_update','assistantMessageEvent':{'type':'toolcall_delta','delta':'private'}})
 emit({'type':'message_update','assistantMessageEvent':{'type':'text_delta','contentIndex':0,'delta':'先说一句。'}})
 deadline=time.monotonic()+2
 while not Path(sys.argv[1]).exists() and time.monotonic()<deadline: time.sleep(.01)
 if not Path(sys.argv[1]).exists(): sys.exit(2)
 emit({'type':'message_update','assistantMessageEvent':{'type':'text_end','contentIndex':0,'content':'先说一句。再说一句。'}})
 emit({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'先说一句。再说一句。'}],'stopReason':'stop'}})
 emit({'type':'agent_settled'})
''', encoding="utf-8")

    async def run():
        pieces = []

        async def on_text(text):
            pieces.append(text)
            marker.write_text("heard", encoding="utf-8")

        async with module().PiAgent([sys.executable, str(fake), str(marker)]) as pi:
            reply = await pi.ask("hello", timeout=3, on_text=on_text)
        assert pieces == ["先说一句。", "再说一句。"]
        assert reply == "".join(pieces)

    asyncio.run(run())


def test_stream_rewrite_fails_instead_of_replaying_changed_text(tmp_path):
    fake = tmp_path / "rewrite.py"
    fake.write_text('''import json, sys
for line in sys.stdin.buffer:
 c=json.loads(line)
 if c['type']=='abort': break
 if c['type']=='prompt':
  for e in [{'type':'response','id':c['id'],'success':True},
   {'type':'message_update','assistantMessageEvent':{'type':'text_delta','contentIndex':0,'delta':'already spoken'}},
   {'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':'replacement'}]}},
   {'type':'agent_settled'}]: print(json.dumps(e),flush=True)
''', encoding="utf-8")

    async def run():
        pieces = []
        async def on_text(text): pieces.append(text)
        async with module().PiAgent([sys.executable, str(fake)]) as pi:
            with pytest.raises(RuntimeError, match="changed.*streamed"):
                await pi.ask("hello", timeout=2, on_text=on_text)
            assert pi.process.returncode is not None
        assert pieces == ["already spoken"]
    asyncio.run(run())
