from pathlib import Path

import pytest

from watcherobot.cli import build_parser, main


def test_template_help_explains_choices(capsys) -> None:
    with pytest.raises(SystemExit) as result:
        build_parser().parse_args(['app', 'init', '--help'])
    assert result.value.code == 0
    output = ' '.join(capsys.readouterr().out.split())
    assert 'base: Minimal Application' in output
    assert 'voice: Voice conversation' in output


@pytest.mark.parametrize('template', ['base', 'voice'])
def test_minimal_cli_generates_independent_application(tmp_path: Path, capsys, template: str) -> None:
    root = tmp_path / template
    assert main(['app', 'init', str(root), '--template', template]) == 0
    assert (root / 'app.json').is_file()
    assert (root / 'icon.svg').is_file()
    if template == 'voice':
        assert (root / 'application/voice.py').is_file()
        assert (root / 'credentials/llm.toml').read_text() == 'api_key = ""\n'
        assert 'credentials/' in capsys.readouterr().out
    else:
        assert not (root / 'credentials').exists()
        assert 'ApplicationContext.from_environment()' in (root / 'app.py').read_text()
        assert 'behavior.play' not in (root / 'app.py').read_text()


@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt])
def test_template_failure_removes_partial_scaffold(tmp_path: Path, monkeypatch, failure) -> None:
    from watcherobot.application import project, templates

    def broken_template(root: Path) -> None:
        (root / 'partial.txt').write_text('unfinished')
        raise failure('template interrupted')

    monkeypatch.setattr(
        templates, 'BUILTIN_TEMPLATES',
        (*templates.BUILTIN_TEMPLATES, templates.ApplicationTemplate('broken', 'Test', broken_template)),
    )
    existing = tmp_path / 'existing.txt'
    existing.write_text('keep')
    with pytest.raises(failure, match='template interrupted'):
        project.init_application_project(
            tmp_path / 'app', app_id='local.test', name='Test', author='Test',
            description='Test', supported_host_platforms=['windows'], template='broken',
        )
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_text() == 'keep'


def test_voice_init_guides_configuration_pairing_then_run(tmp_path, capsys) -> None:
    root = tmp_path / 'my_voice_app'
    assert main(['app', 'init', str(root), '--template', 'voice']) == 0
    output = capsys.readouterr().out
    assert output.index('credentials/asr.toml') < output.index('watcherobot app run')
    assert output.index('watcherobot robot pair 123456') < output.index('watcherobot app run')
    readme = (root / 'README.md').read_text(encoding='utf-8')
    assert 'watcherobot robot pair 123456' in readme
    assert 'watcherobot robot status' in readme


def test_default_template_is_packaged_five_file_base(tmp_path, capsys):
    from importlib.resources import files
    import json
    from watcherobot.application.templates import DEFAULT_TEMPLATE

    assert DEFAULT_TEMPLATE == 'base'
    expected = {'app.json', 'app.py', 'README.md', 'icon.svg', '.gitignore'}
    assets = files('watcherobot').joinpath('templates/base')
    assert {item.name for item in assets.iterdir() if item.is_file()} == expected
    root = tmp_path / 'my_app'
    assert main(['app', 'init', str(root)]) == 0
    assert {item.name for item in root.iterdir()} == expected
    assert (root / 'app.py').read_bytes() == assets.joinpath('app.py').read_bytes()
    assert json.loads((root / 'app.json').read_text())['id'] == 'local.my_app'
    assert 'Hello' not in (root / 'README.md').read_text()


def test_voice_extends_base_without_sdk_development_artifacts(tmp_path, capsys):
    base, voice = tmp_path / 'base', tmp_path / 'voice'
    assert main(['app', 'init', str(base)]) == 0
    assert main(['app', 'init', str(voice), '--template', 'voice']) == 0
    assert {item.name for item in voice.iterdir()} == {
        'app.json', 'app.py', 'README.md', 'icon.svg', '.gitignore',
        'application', 'config', 'credentials', 'credential-examples', 'prompts',
    }
    assert (voice / 'icon.svg').read_bytes() == (base / 'icon.svg').read_bytes()
    assert (voice / '.gitignore').read_text().startswith((base / '.gitignore').read_text())


def test_base_entry_can_be_imported_and_stops_with_context_cleanup(tmp_path, monkeypatch):
    import asyncio
    import importlib.util
    import logging
    from types import SimpleNamespace

    root = tmp_path / 'app'
    assert main(['app', 'init', str(root)]) == 0
    spec = importlib.util.spec_from_file_location('generated_base', root / 'app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # Import must not start a Runtime or need credentials.
    observed = []

    class Context:
        logger = logging.getLogger('test.base')
        shutdown_requested = False
        async def __aenter__(self):
            observed.append('started')
            asyncio.get_running_loop().call_soon(setattr, self, 'shutdown_requested', True)
            return self
        async def __aexit__(self, *args):
            observed.append('closed')

    monkeypatch.setattr(module, 'ApplicationContext', SimpleNamespace(from_environment=Context))
    async def run():
        await asyncio.wait_for(module.main(), 1)
    asyncio.run(run())
    assert observed == ['started', 'closed']
