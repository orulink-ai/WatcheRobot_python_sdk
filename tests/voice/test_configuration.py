from pathlib import Path

import pytest

from watcherobot.application.project import init_application_project
from watcherobot.distribution.source_files import collect_application_source_files
from watcherobot.voice.configuration import ConfigurationError, load_configuration


def project(tmp_path: Path) -> Path:
    root = tmp_path / 'voice'
    init_application_project(root, app_id='local.voice', name='Voice', author='Test',
                             description='Voice test', supported_host_platforms=['windows'],
                             template='voice')
    return root


def test_template_and_missing_credentials(tmp_path):
    root = project(tmp_path)
    assert (root / 'application/voice.py').is_file()
    assert (root / 'prompts/system.md').is_file()
    with pytest.raises(ConfigurationError, match='credentials/asr.toml.*app_id'):
        load_configuration(root)


def configured(root):
    (root / 'credentials/asr.toml').write_text('app_id = "test"\naccess_token = "secret-asr"', encoding='utf-8')
    (root / 'credentials/llm.toml').write_text('api_key = "$' + '{env:VOICE_TEST_KEY}"', encoding='utf-8')
    (root / 'credentials/tts.toml').write_text('app_id = "test"\naccess_token = "secret-tts"', encoding='utf-8')


def test_environment_prompt_and_secret_repr(tmp_path, monkeypatch):
    root = project(tmp_path)
    configured(root)
    monkeypatch.setenv('VOICE_TEST_KEY', 'secret-llm')
    config = load_configuration(root)
    assert config.llm.credentials['api_key'] == 'secret-llm'
    assert 'watcher' in config.prompt
    assert 'secret-' not in repr(config)
    (root / 'prompts/system.md').write_text('{{missing}}', encoding='utf-8')
    with pytest.raises(ConfigurationError, match='missing'):
        load_configuration(root)


def test_publish_excludes_credentials_even_without_ignore_file(tmp_path):
    root = project(tmp_path)
    configured(root)
    for name in ('.gitignore', '.watcherignore'):
        (root / name).unlink(missing_ok=True)
    files = {p.as_posix() for p in collect_application_source_files(root)}
    assert not any(p.startswith('credentials/') for p in files)
    assert 'config/models/asr.toml' in files
    assert 'prompts/system.md' in files


def test_unknown_config_field_and_unbounded_recording_rejected(tmp_path, monkeypatch):
    root = project(tmp_path)
    configured(root)
    monkeypatch.setenv('VOICE_TEST_KEY', 'secret')
    path = root / 'config/conversation.toml'
    path.write_text('max_utterance_seconds = -1', encoding='utf-8')
    with pytest.raises(ConfigurationError, match='max_utterance_seconds'):
        load_configuration(root)
    path.write_text('histroy_turns = 4', encoding='utf-8')
    with pytest.raises(ConfigurationError, match='histroy_turns'):
        load_configuration(root)


@pytest.mark.parametrize('content,field', [
    ('provider = "qwen"\nmodel = ""', 'model'),
    ('provider = "qwen"\nmodel = "future"\n[parameters]\ntemperature = -1', 'temperature'),
    ('provider = "qwen"\nmodel = "future"\n[parameters]\nmax_tokens = true', 'max_tokens'),
    ('provider = "openai_chat"\nmodel = "future"', 'base_url'),
])
def test_model_fields_validated_before_start(tmp_path, monkeypatch, content, field):
    root = project(tmp_path)
    configured(root)
    monkeypatch.setenv('VOICE_TEST_KEY', 'placeholder')
    (root / 'config/models/llm.toml').write_text(content, encoding='utf-8')
    with pytest.raises(ConfigurationError, match=field):
        load_configuration(root)


def test_template_contains_runtime_imports_and_own_readme(tmp_path):
    import importlib.util

    root = project(tmp_path)
    spec = importlib.util.spec_from_file_location('generated_voice_entry', root / 'application/voice.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)
    assert '语音 Application' in (root / 'README.md').read_text(encoding='utf-8')


def test_installed_credentials_are_separate_and_not_overwritten(tmp_path, monkeypatch):
    from watcherobot.voice.configuration import credential_directory

    root = project(tmp_path)
    user_credentials = tmp_path / 'user-credentials'
    user_credentials.mkdir()
    secret_file = user_credentials / 'llm.toml'
    secret_file.write_text('api_key = "test-placeholder"', encoding='utf-8')
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', str(user_credentials))
    before = secret_file.read_bytes()
    assert credential_directory(root, 'local.voice', installed=True) == user_credentials.resolve()
    from watcherobot.voice.template import write_voice_template
    write_voice_template(root)
    assert secret_file.read_bytes() == before


def test_relative_credential_override_resolves_from_project(tmp_path, monkeypatch):
    from watcherobot.voice.configuration import credential_directory

    root = project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WATCHER_VOICE_CREDENTIALS_DIR', 'private-credentials')
    assert credential_directory(root, 'local.voice') == root / 'private-credentials'


def test_relative_credentials_argument_resolves_from_project(tmp_path, monkeypatch):
    root = project(tmp_path)
    configured(root)
    monkeypatch.setenv('VOICE_TEST_KEY', 'placeholder')
    monkeypatch.chdir(tmp_path)
    config = load_configuration(root, credentials_dir=Path('credentials'))
    assert config.llm.credentials['api_key'] == 'placeholder'


def test_template_does_not_require_a_global_language(tmp_path, monkeypatch):
    from watcherobot.voice.configuration import read_toml

    root = project(tmp_path)
    configured(root)
    monkeypatch.setenv('VOICE_TEST_KEY', 'test-placeholder')
    persona = read_toml(root / 'config/persona.toml')
    assert 'language' not in persona
    prompt = (root / 'prompts/system.md').read_text(encoding='utf-8')
    assert '{{language}}' not in prompt
    config = load_configuration(root)
    assert config.asr.language == config.llm.language == config.tts.language == ''
    assert '请使用 中文' not in config.prompt


def test_prompt_and_provider_languages_remain_independent(tmp_path):
    root = project(tmp_path)
    (root / 'config/models/asr.toml').write_text(
        'provider="deepgram"\nmodel="custom-asr"\nlanguage="ja"\n', encoding='utf-8')
    (root / 'config/models/tts.toml').write_text(
        'provider="elevenlabs"\nmodel="custom-tts"\nvoice_id="custom-voice"\nlanguage="en"\n', encoding='utf-8')
    for kind in ('asr', 'llm', 'tts'):
        (root / f'credentials/{kind}.toml').write_text('api_key="test-placeholder"', encoding='utf-8')
    (root / 'config/persona.toml').write_text('name="watcher"\ninstruction="Translate into French."', encoding='utf-8')
    (root / 'prompts/system.md').write_text('{{instruction}}', encoding='utf-8')
    config = load_configuration(root)
    assert config.prompt == 'Translate into French.'
    assert config.asr.language == 'ja'
    assert config.tts.language == 'en'
    assert config.llm.language == ''
