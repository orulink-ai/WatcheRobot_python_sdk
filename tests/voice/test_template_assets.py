"""The template is a real, packaged project tree, not embedded source strings."""
from importlib.resources import files
from pathlib import Path

from watcherobot.application.project import init_application_project


def test_voice_assets_are_real_files_and_generate_unchanged(tmp_path: Path) -> None:
    assets = files('watcherobot').joinpath('templates/voice')
    assert assets.joinpath('application/voice.py').is_file()
    root = tmp_path / 'my_voice_app'
    init_application_project(root, app_id='local.voice', name='Voice', author='Test',
                             description='Voice', supported_host_platforms=['windows'], template='voice')
    for relative in ('app.py', 'application/voice.py', 'config/models/asr.toml',
                     'config/models/llm.toml', 'config/models/tts.toml', 'README.md'):
        assert (root / relative).read_bytes() == assets.joinpath(relative).read_bytes()
    for kind in ('asr', 'llm', 'tts'):
        assert (root / f'credentials/{kind}.toml').read_bytes() == (root / f'credential-examples/{kind}.toml').read_bytes()
    assert 'my_voice_app/' in (root / 'README.md').read_text(encoding='utf-8')


def test_generated_readme_defaults_match_configuration(tmp_path: Path) -> None:
    import re
    from watcherobot.voice.configuration import read_toml
    import tomli

    root = tmp_path / 'voice'
    init_application_project(root, app_id='local.voice', name='Voice', author='Test',
                             description='Voice', supported_host_platforms=['windows'], template='voice')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    for kind in ('asr', 'llm', 'tts'):
        section = readme.split(f'`config/models/{kind}.toml`：', 1)[1]
        text = re.search(r'```toml\n(.*?)\n```', section, re.S).group(1)
        assert tomli.loads(text) == read_toml(root / f'config/models/{kind}.toml')


def test_generated_application_runs_one_offline_voice_turn(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    import importlib.util
    import logging
    from types import SimpleNamespace
    from watcherobot.voice.contracts import AudioChunk, Transcript
    from watcherobot.voice.providers import ProviderRegistry
    import watcherobot.voice.device as device_module

    root = tmp_path / 'voice'
    init_application_project(root, app_id='local.voice', name='Voice', author='Test',
                             description='Voice', supported_host_platforms=['windows'], template='voice')
    for kind in ('asr', 'tts'):
        (root / f'credentials/{kind}.toml').write_text('app_id="test"\naccess_token="test-placeholder"')
    (root / 'credentials/llm.toml').write_text('api_key="test-placeholder"')
    spec = importlib.util.spec_from_file_location('generated_voice', root / 'application/voice.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observed = []

    class ASR:
        async def transcribe(self, audio):
            async for frame in audio:
                observed.append(('audio', frame))
            yield Transcript('你好')
        async def close(self):
            observed.append('asr_closed')

    class LLM:
        async def generate(self, messages):
            assert messages[-1].content == '你好'
            assert 'watcher' in messages[0].content
            yield '你好，我是 watcher。'
        async def close(self):
            observed.append('llm_closed')

    class TTS:
        async def synthesize(self, text):
            assert text == '你好，我是 watcher。'
            yield AudioChunk(b'\x00\x00' * 32)
        async def close(self):
            observed.append('tts_closed')

    class Device:
        async def ready(self):
            return True
        async def utterance(self):
            yield b'\x00\x01'
        async def play(self, pcm):
            observed.append(('played', pcm))
        async def stop(self):
            pass

    class Context:
        robot = None
        shutdown_requested = False
        logger = logging.getLogger('test.generated_voice')
        async def receive(self, timeout):
            await asyncio.sleep(.01)
            self.shutdown_requested = any(isinstance(item, tuple) and item[0] == 'played' for item in observed)
            return None
        async def __aenter__(self):
            self.desktop = self
            return self
        async def __aexit__(self, *args):
            pass

    registry = ProviderRegistry()
    registry.register_asr('volcengine', lambda config: ASR())
    registry.register_llm('qwen', lambda config: LLM())
    registry.register_tts('volcengine', lambda config: TTS())
    monkeypatch.setattr(module, 'create_registry', lambda: registry)
    monkeypatch.setattr(module, 'ApplicationContext', SimpleNamespace(from_environment=Context))
    monkeypatch.setattr(device_module, 'SDKVoiceDevice', lambda *args: Device())

    async def run():
        await asyncio.wait_for(module.main(root), 2)
    asyncio.run(run())
    assert ('played', b'\x00\x00' * 32) in observed
    assert {'asr_closed', 'llm_closed', 'tts_closed'} <= set(item for item in observed if isinstance(item, str))


def test_cli_creates_voice_project_without_network(tmp_path: Path, monkeypatch, capsys) -> None:
    import socket
    from watcherobot.cli import main

    def fail_network(*args, **kwargs):
        raise AssertionError('Built-in template initialization must work offline')

    monkeypatch.setattr(socket.socket, 'connect', fail_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', fail_network)
    monkeypatch.setattr(socket, 'getaddrinfo', fail_network)
    root = tmp_path / 'offline_voice'
    assert main(['app', 'init', str(root), '--template', 'voice']) == 0
    assert (root / 'application/voice.py').is_file()
    assert (root / 'credentials/llm.toml').read_text() == 'api_key = ""\n'
    assert 'watcherobot app run' in capsys.readouterr().out
