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
            assert pi.process.returncode is not None
            # This unresponsive process must be killed, not allowed to finish
            # its sleep and exit normally after the shutdown deadline.
            assert pi.process.returncode != 0
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
