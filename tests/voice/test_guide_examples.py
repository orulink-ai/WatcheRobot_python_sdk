"""Execute the documented extension against the generated Application contract."""
import asyncio
import re
from pathlib import Path

from watcherobot.application.project import init_application_project
from watcherobot.voice.configuration import load_configuration
from watcherobot.voice.contracts import Message
from watcherobot.voice.providers import ProviderRegistry


def test_documented_local_llm_runs_in_generated_project(tmp_path: Path) -> None:
    root = tmp_path / 'app'
    init_application_project(root, app_id='local.docs', name='Docs', author='Test',
                             description='Test', supported_host_platforms=['windows'], template='voice')
    guide = (Path(__file__).resolve().parents[2] / 'docs/voice-template.zh-CN.md').read_text(encoding='utf-8')
    section = guide.split('## 二次开发：一个完整的本地 LLM 适配器', 1)[1]
    code = re.search(r'```python\n(.*?)\n```', section, re.S).group(1)
    namespace = {}
    exec(compile(code, 'application/local_llm.py', 'exec'), namespace)
    for kind in ('asr', 'tts'):
        (root / f'credentials/{kind}.toml').write_text('app_id = "test"\naccess_token = "test-placeholder"')
    (root / 'credentials/llm.toml').write_text('')
    path = root / 'config/models/llm.toml'
    path.write_text(path.read_text().replace('provider = "qwen"', 'provider = "local_demo"'))
    config = load_configuration(root)
    registry = ProviderRegistry.builtin()
    registry.register_llm('local_demo', namespace['LocalLLM'])

    async def run():
        llm = registry.create_llm(config.llm)
        try:
            reply = ''.join([piece async for piece in llm.generate([Message('user', '你好')])])
            assert reply == '你好，我是 watcher。自定义 LLM 适配器已接入。'
        finally:
            await llm.close()
    asyncio.run(run())
