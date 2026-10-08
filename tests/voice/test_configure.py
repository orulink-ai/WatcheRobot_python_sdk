"""Interactive configuration must remain local and preserve existing credentials."""
from pathlib import Path

import pytest

from watcherobot import cli
from watcherobot.voice.configuration import load_configuration, read_toml


@pytest.fixture
def project(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'voice'
    monkeypatch.delenv('WATCHER_VOICE_CREDENTIALS_DIR', raising=False)
    assert cli.main(['app', 'init', str(root), '--template', 'voice']) == 0
    monkeypatch.setattr(cli, '_is_interactive_terminal', lambda: True)
    monkeypatch.setattr(cli, 'ensure_runtime', lambda *a, **kw: pytest.fail('must not start Runtime'))
    capsys.readouterr()
    return root


def answers(monkeypatch, values):
    prompts = []
    source = iter(values)
    def secret(prompt):
        prompts.append(prompt)
        value = next(source)
        if isinstance(value, BaseException):
            raise value
        return value
    monkeypatch.setattr(cli, 'getpass', secret)
    return prompts


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_configure_defaults_and_hidden_input(project, monkeypatch, capsys):
    before = snapshot(project)
    values = ['test-app', 'test-asr-token', 'test-llm-key', 'test-app', 'test-tts-token']
    prompts = answers(monkeypatch, values)
    monkeypatch.chdir(project)
    assert cli.main(['app', 'configure']) == 0
    config = load_configuration(project)
    assert config.asr.credentials['access_token'] == values[1]
    assert config.llm.credentials['api_key'] == values[2]
    assert config.tts.credentials['access_token'] == values[4]
    for path, content in before.items():
        if path.parts[0] != 'credentials':
            assert (project / path).read_bytes() == content
    output = capsys.readouterr()
    for value in values:
        assert value not in output.out + output.err + ''.join(prompts)
    assert '云服务' in output.out
    if __import__('os').name != 'nt':
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in (project / 'credentials').glob('*.toml'))


def test_blank_preserves_existing_files_and_environment_reference(project, monkeypatch):
    for kind in ('asr', 'tts'):
        (project / f'credentials/{kind}.toml').write_text('app_id="test"\naccess_token="existing"\n# keep comment\n')
    llm = project / 'credentials/llm.toml'
    llm.write_text('api_key="${env:TEST_VOICE_KEY}"\n# keep reference\n')
    monkeypatch.setenv('TEST_VOICE_KEY', 'resolved-test-value')
    before = snapshot(project)
    answers(monkeypatch, [''] * 5)
    assert cli.main(['app', 'configure', str(project)]) == 0
    assert snapshot(project) == before


@pytest.mark.parametrize('error', [KeyboardInterrupt(), EOFError()])
def test_cancel_before_saving_preserves_all_files(project, monkeypatch, error):
    before = snapshot(project)
    answers(monkeypatch, ['new-app', 'new-token', error])
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before


def test_invalid_configuration_is_not_saved(project, monkeypatch, capsys):
    (project / 'config/models/llm.toml').write_text('provider="qwen"\nmodel=""\n')
    before = snapshot(project)
    answers(monkeypatch, ['test-app', 'token', 'key', 'test-app', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before
    assert 'model' in capsys.readouterr().err


def test_unset_environment_reference_not_replaced_or_saved(project, monkeypatch):
    monkeypatch.delenv('TEST_VOICE_ABSENT', raising=False)
    before = snapshot(project)
    answers(monkeypatch, ['test-app', 'token', '${env:TEST_VOICE_ABSENT}', 'test-app', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before


@pytest.mark.parametrize('use_token', [False, True])
@pytest.mark.parametrize('service', [None, 'asr'])
def test_aliyun_credentials_follow_current_provider(project, monkeypatch, use_token, service):
    (project / 'config/models/asr.toml').write_text('provider="aliyun"\n')
    (project / 'credentials/asr.toml').write_text('app_key=""\n' + ('token="existing-token"\n' if use_token else ''))
    entries = ['app-key', ''] if use_token else ['app-key', 'access-id', 'access-secret']
    prompts = answers(monkeypatch, entries if service else [*entries, 'llm-key', 'app-id', 'tts-token'])
    args = ['app', 'configure', str(project)] + (['--service', service] if service else [])
    assert cli.main(args) == 0
    credentials = read_toml(project / 'credentials/asr.toml')
    assert credentials['app_key'] == 'app-key'
    if use_token:
        assert credentials['token'] == 'existing-token'
        assert not any('access_key_id' in p for p in prompts)
    else:
        assert credentials['access_key_secret'] == 'access-secret'


def test_required_empty_input_reprompts_and_special_characters_roundtrip(project, monkeypatch):
    key = 'test-quote"backslash\\line\nvalue'
    prompts = answers(monkeypatch, ['', 'app-id', 'token', key, 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    assert len(prompts) == 6
    assert read_toml(project / 'credentials/llm.toml')['api_key'] == key


def test_override_matches_runtime_and_does_not_write_project_credentials(project, monkeypatch):
    target = project.parent / 'private'
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', str(target))
    before = snapshot(project)
    answers(monkeypatch, ['app-id', 'token', 'key', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    after = snapshot(project)
    marker = Path('credentials/.source.toml')
    assert {p: b for p, b in after.items() if p != marker} == before
    assert read_toml(project / marker)['directory'] == str(target.resolve())
    assert load_configuration(project, credentials_dir=target).llm.credentials['api_key'] == 'key'


@pytest.mark.parametrize('relative', ['private-credentials', '.', 'config/models'])
def test_unsafe_project_credential_directory_rejected_before_writing(project, monkeypatch, relative):
    before = snapshot(project)
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', relative)
    answers(monkeypatch, ['app-id', 'token', 'key', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before


def test_protected_nested_credentials_are_excluded_from_publishing(project, monkeypatch):
    from watcherobot.distribution.source_files import collect_application_source_files

    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', 'credentials/team')
    answers(monkeypatch, ['app-id', 'token', 'key', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    assert read_toml(project / 'credentials/team/llm.toml')['api_key'] == 'key'
    assert not any(p.parts[0] == 'credentials' for p in collect_application_source_files(project))


def test_single_service_cannot_switch_global_credential_source(project, monkeypatch, capsys):
    from watcherobot.voice.configuration import credential_directory

    answers(monkeypatch, ['app-id', 'asr-token', 'key', 'app-id', 'tts-token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    before = snapshot(project)
    target = project.parent / 'new-private'
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', str(target))
    answers(monkeypatch, ['new-key'])
    assert cli.main(['app', 'configure', str(project), '--service', 'llm']) == 2
    assert '--service' in capsys.readouterr().err
    assert snapshot(project) == before
    assert not target.exists()
    directory = credential_directory(project, 'local.voice', use_environment=False)
    config = load_configuration(project, credentials_dir=directory)
    assert config.asr.credentials['access_token'] == 'asr-token'
    assert config.tts.credentials['access_token'] == 'tts-token'

    # Explicit full configuration may switch all services together.
    answers(monkeypatch, ['app-id', 'new-asr', 'new-key', 'app-id', 'new-tts'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    assert credential_directory(project, 'local.voice', use_environment=False) == target.resolve()


def test_configured_directory_survives_a_daemon_with_different_environment(project, monkeypatch):
    from watcherobot.voice.configuration import credential_directory

    target = project.parent / 'private'
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', str(target))
    answers(monkeypatch, ['app-id', 'token', 'key', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', str(project.parent / 'stale'))
    directory = credential_directory(project, 'local.voice', use_environment=False)
    assert directory == target.resolve()
    assert load_configuration(project, credentials_dir=directory).llm.credentials['api_key'] == 'key'

    # A later terminal need not repeat the directory override to edit this app.
    monkeypatch.delenv('WATCHER_VOICE_CREDENTIALS_DIR')
    answers(monkeypatch, ['replacement-key'])
    assert cli.main(['app', 'configure', str(project), '--service', 'llm']) == 0
    assert load_configuration(project, credentials_dir=directory).llm.credentials['api_key'] == 'replacement-key'


def test_generated_application_uses_saved_directory_with_stale_daemon_environment(project, monkeypatch):
    import asyncio
    import importlib.util

    target = project.parent / 'private'
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', str(target))
    answers(monkeypatch, ['app-id', 'token', 'key', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', str(project.parent / 'stale'))
    spec = importlib.util.spec_from_file_location('voice_entry_review', project / 'application/voice.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Context:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    class Application:
        def __init__(self, app, config, registry):
            calls.append(config.llm.credentials['api_key'])
        async def run(self):
            pass

    monkeypatch.setattr(module.ApplicationContext, 'from_environment', lambda: Context())
    monkeypatch.setattr(module, 'VoiceApplication', Application)
    asyncio.run(module.main(project))
    assert calls == ['key']


def test_failed_override_save_does_not_change_runtime_directory(project, monkeypatch):
    from watcherobot.voice import configure
    from watcherobot.voice.configuration import credential_directory

    target = project.parent / 'private'
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', str(target))
    replace = configure.os.replace
    def fail_marker(source, destination):
        if Path(destination).name == '.source.toml':
            raise OSError('simulated marker write failure')
        return replace(source, destination)
    monkeypatch.setattr(configure.os, 'replace', fail_marker)
    before = snapshot(project)
    answers(monkeypatch, ['app-id', 'token', 'key', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before
    assert credential_directory(project, 'local.voice', use_environment=False) == project / 'credentials'
    assert not list(target.glob('*.toml'))


def test_environment_references_explain_daemon_startup_requirement(project, monkeypatch, capsys):
    monkeypatch.setenv('REVIEW_VOICE_KEY', 'test-placeholder')
    answers(monkeypatch, ['app-id', 'token', '${env:REVIEW_VOICE_KEY}', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    output = capsys.readouterr().out
    assert 'Daemon' in output and '启动前' in output
    assert 'test-placeholder' not in output


def test_base_template_is_unchanged_and_has_no_configuration(project, tmp_path, monkeypatch):
    base = tmp_path / 'base'
    assert cli.main(['app', 'init', str(base)]) == 0
    before = snapshot(base)
    answers(monkeypatch, [])
    assert cli.main(['app', 'configure', str(base)]) == 2
    assert snapshot(base) == before


def test_noninteractive_configuration_fails_without_writes(project, monkeypatch):
    before = snapshot(project)
    monkeypatch.setattr(cli, '_is_interactive_terminal', lambda: False)
    answers(monkeypatch, [])
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before


def test_getpass_cannot_fall_back_to_echo(project, monkeypatch):
    import warnings
    from getpass import GetPassWarning
    before = snapshot(project)
    def unsafe_prompt(prompt):
        warnings.warn('Cannot hide input', GetPassWarning)
        pytest.fail('must not continue with echoed input')
    monkeypatch.setattr(cli, 'getpass', unsafe_prompt)
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before


def test_failed_write_rolls_back_all_files(project, monkeypatch):
    from watcherobot.voice import configure
    before = snapshot(project)
    replace = configure.os.replace
    failed = False
    def fail_once(source, destination):
        nonlocal failed
        if Path(destination).name == 'llm.toml' and not failed:
            failed = True
            raise OSError('simulated disk error')
        return replace(source, destination)
    monkeypatch.setattr(configure.os, 'replace', fail_once)
    answers(monkeypatch, ['app-id', 'token', 'key', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before


def test_custom_provider_retains_custom_credential_fields(project, monkeypatch):
    (project / 'config/models/llm.toml').write_text('provider="custom"\n')
    (project / 'credentials/llm.toml').write_text('tenant=""\ncustom_token=""\n')
    answers(monkeypatch, ['app-id', 'token', 'test-tenant', 'test-custom-token', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    assert load_configuration(project).llm.credentials == {
        'tenant': 'test-tenant', 'custom_token': 'test-custom-token',
    }


def test_local_llm_allows_no_api_key(project, monkeypatch):
    (project / 'config/models/llm.toml').write_text(
        'provider="openai_chat"\nmodel="local-model"\nbase_url="http://localhost:1234/v1"\n')
    answers(monkeypatch, ['app-id', 'token', '', 'app-id', 'token'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    assert load_configuration(project).llm.credentials['api_key'] == ''


def test_configure_dispatch_supports_other_templates(project, monkeypatch):
    from watcherobot.application import templates
    observed = []
    template = templates.ApplicationTemplate(
        'other', 'Test', configuration_files=('app.json',),
        configure=lambda root, prompt, output, service: observed.append(root),
    )
    monkeypatch.setattr(templates, 'BUILTIN_TEMPLATES', (template,))
    assert cli.main(['app', 'configure', str(project)]) == 0
    assert observed == [project.resolve()]


def test_credential_symlink_is_not_followed(project, tmp_path, monkeypatch):
    target = tmp_path / 'outside.toml'
    target.write_text('api_key="outside"\n')
    path = project / 'credentials/llm.toml'
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable')
    answers(monkeypatch, [])
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert target.read_text() == 'api_key="outside"\n'


def test_external_edit_during_prompt_is_not_overwritten(project, monkeypatch):
    target = project / 'credentials/llm.toml'
    values = iter(['app-id', 'token', 'new-key', 'app-id', 'token'])
    def edit_during_prompt(prompt):
        if 'api_key' in prompt:
            target.write_text('api_key="external-edit"\n')
        return next(values)
    before_asr = (project / 'credentials/asr.toml').read_bytes()
    monkeypatch.setattr(cli, 'getpass', edit_during_prompt)
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert target.read_text() == 'api_key="external-edit"\n'
    assert (project / 'credentials/asr.toml').read_bytes() == before_asr
    assert not list((project / 'credentials').glob('.voice-config-*'))


def test_invalid_project_reports_error_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, '_is_interactive_terminal', lambda: True)
    assert cli.main(['app', 'configure', str(tmp_path)]) == 2
    assert 'Traceback' not in capsys.readouterr().err


@pytest.mark.parametrize('service', [None, 'llm'])
def test_windows_console_input_handles_unicode_backspace_and_enter(project, monkeypatch, service):
    """Exercise CPython's Windows getpass path with a fake console driver."""
    import getpass
    import sys
    from types import SimpleNamespace

    typed = iter('测试密钥x\b\r' if service else 'app-id\rtest-asr\r测试密钥x\b\rapp-id\rtest-tts\r')
    console_output = []
    monkeypatch.setattr(getpass, 'msvcrt', SimpleNamespace(
        getwch=lambda: next(typed), putwch=console_output.append,
    ), raising=False)
    monkeypatch.setattr(sys, 'stdin', sys.__stdin__)
    monkeypatch.setattr(cli, 'getpass', getpass.win_getpass)
    args = ['app', 'configure', str(project)] + (['--service', service] if service else [])
    assert cli.main(args) == 0
    assert read_toml(project / 'credentials/llm.toml')['api_key'] == '测试密钥'
    assert '测试密钥' not in ''.join(console_output)
    before = snapshot(project)
    typed = iter('\r' * (1 if service else 5))
    assert cli.main(args) == 0
    assert snapshot(project) == before


def test_windows_console_ctrl_c_does_not_save(project, monkeypatch):
    import getpass
    import sys
    from types import SimpleNamespace

    typed = iter('app-id\rtest-asr\r\x03')
    monkeypatch.setattr(getpass, 'msvcrt', SimpleNamespace(
        getwch=lambda: next(typed), putwch=lambda character: None,
    ), raising=False)
    monkeypatch.setattr(sys, 'stdin', sys.__stdin__)
    monkeypatch.setattr(cli, 'getpass', getpass.win_getpass)
    before = snapshot(project)
    assert cli.main(['app', 'configure', str(project)]) == 2
    assert snapshot(project) == before


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
def test_platform_user_credentials_match_runtime_with_spaces(project, monkeypatch, tmp_path, platform):
    from watcherobot.voice import configuration

    for file in (project / 'credentials').iterdir():
        file.unlink()
    (project / 'credentials').rmdir()
    home = tmp_path / '用户 Home'
    local_app_data = home / 'AppData' / 'Local'
    monkeypatch.setattr(configuration.sys, 'platform', platform)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('LOCALAPPDATA', str(local_app_data))
    base = local_app_data if platform == 'win32' else home / 'Library/Application Support'
    target = base / 'watcherobot/applications/local.voice/credentials'
    answers(monkeypatch, ['app-id', 'test-asr', 'test-llm', 'app-id', 'test-tts'])
    assert cli.main(['app', 'configure', str(project)]) == 0
    assert configuration.credential_directory(project, 'local.voice') == target
    assert load_configuration(project, credentials_dir=target).llm.credentials['api_key'] == 'test-llm'
    assert not (project / 'credentials').exists()


@pytest.mark.parametrize('service', ['asr', 'llm', 'tts'])
@pytest.mark.parametrize('other_state', ['empty', 'invalid', 'missing'])
def test_single_service_ignores_other_configuration(project, monkeypatch, service, other_state):
    for other in {'asr', 'llm', 'tts'} - {service}:
        for folder in ('credentials', 'config/models'):
            path = project / folder / f'{other}.toml'
            if other_state == 'invalid':
                path.write_text('not valid TOML !')
            elif other_state == 'missing':
                path.unlink()
    (project / 'prompts/system.md').write_text('{{undefined}}')
    before = snapshot(project)
    values = ['selected-key'] if service == 'llm' else ['selected-app', 'selected-token']
    prompts = answers(monkeypatch, values)
    assert cli.main(['app', 'configure', str(project), '--service', service]) == 0
    assert len(prompts) == len(values)
    selected = Path('credentials') / f'{service}.toml'
    result = snapshot(project)
    assert {p: b for p, b in result.items() if p != selected} == {
        p: b for p, b in before.items() if p != selected
    }
    assert result[selected] != before[selected]


@pytest.mark.parametrize('model', [
    'provider="qwen"\nmodel=""\n',
    'provider="qwen"\nmodel="qwen-flash"\n[parameters]\ntemperature=9\n',
])
def test_single_service_still_validates_selected_model(project, monkeypatch, model):
    (project / 'config/models/llm.toml').write_text(model)
    before = snapshot(project)
    answers(monkeypatch, ['test-key'])
    assert cli.main(['app', 'configure', str(project), '--service', 'llm']) == 2
    assert snapshot(project) == before


def test_single_service_keeps_env_reference_without_reading_other_credentials(project, monkeypatch):
    path = project / 'credentials/llm.toml'
    path.write_text('api_key="${env:SELECTED_LLM_KEY}"\n# retain\n')
    monkeypatch.setenv('SELECTED_LLM_KEY', 'test-key')
    (project / 'credentials/asr.toml').write_text('app_id="${env:UNSET_OTHER_KEY}"\n')
    before = snapshot(project)
    answers(monkeypatch, [''])
    assert cli.main(['app', 'configure', str(project), '--service', 'llm']) == 0
    assert snapshot(project) == before


def test_single_service_cancel_preserves_all_files(project, monkeypatch):
    before = snapshot(project)
    answers(monkeypatch, ['test-app', KeyboardInterrupt()])
    assert cli.main(['app', 'configure', str(project), '--service', 'asr']) == 2
    assert snapshot(project) == before


def test_invalid_service_rejected_before_prompt(project, monkeypatch):
    before = snapshot(project)
    answers(monkeypatch, [])
    with pytest.raises(SystemExit) as result:
        cli.main(['app', 'configure', str(project), '--service', 'invalid'])
    assert result.value.code == 2
    assert snapshot(project) == before
