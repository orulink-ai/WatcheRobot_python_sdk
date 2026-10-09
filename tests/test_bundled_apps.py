from pathlib import Path
from types import SimpleNamespace

import pytest

from watcherobot import cli
from watcherobot import bundled_apps


def test_demo_menu_switches_repeatedly_without_restarting_cli(monkeypatch):
    choices = iter(['1', '2', '1', 'q'])
    calls = []
    monkeypatch.setattr(cli, '_is_interactive_terminal', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: next(choices))
    monkeypatch.setattr(cli, 'run_demo', lambda name: calls.append(name) or 0)
    assert cli.main(['demo']) == 0
    assert calls == ['sdk-test-bench', 'expression-lab', 'sdk-test-bench']


def test_demo_menu_survives_invalid_input_and_failed_launch(monkeypatch, capsys):
    choices = iter(['invalid', '1', '2', 'q'])
    calls = []
    monkeypatch.setattr(cli, '_is_interactive_terminal', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: next(choices))

    def launch(name):
        calls.append(name)
        if name == 'sdk-test-bench':
            raise cli.CliError('stop failed')
        return 0

    monkeypatch.setattr(cli, 'run_demo', launch)
    assert cli.main(['demo']) == 0
    assert calls == ['sdk-test-bench', 'expression-lab']
    assert 'stop failed' in capsys.readouterr().err


def test_demo_menu_noninteractive_does_not_touch_runtime(monkeypatch):
    monkeypatch.setattr(cli, '_is_interactive_terminal', lambda: False)
    monkeypatch.setattr(cli, 'ensure_runtime', lambda: pytest.fail('must not start Daemon'))
    assert cli.main(['demo']) == 2


@pytest.mark.parametrize('exception, expected', [(EOFError, 0), (KeyboardInterrupt, 130)])
def test_demo_menu_exit_keeps_current_app(monkeypatch, exception, expected):
    monkeypatch.setattr(cli, '_is_interactive_terminal', lambda: True)
    monkeypatch.setattr(cli, 'ensure_runtime', lambda: pytest.fail('must not touch Daemon'))

    def cancelled(prompt):
        raise exception

    monkeypatch.setattr('builtins.input', cancelled)
    assert cli.main(['demo']) == expected


def test_demo_menu_can_stop_current_app_without_stopping_daemon(monkeypatch):
    choices = iter(['0', 'q'])
    requests = []
    monkeypatch.setattr(cli, '_is_interactive_terminal', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: next(choices))
    monkeypatch.setattr(cli, '_live_runtime_state', lambda: SimpleNamespace(control_url='http://daemon'))
    monkeypatch.setattr(cli, '_request_json', lambda url, path, **kwargs: requests.append(path) or {})
    assert cli.main(['demo']) == 0
    assert requests == ['/daemon/application/stop']


@pytest.mark.parametrize('name', ['sdk-test-bench', 'expression-lab'])
def test_demo_switch_uses_only_daemon_management(name, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, 'default_runtime_state_root', lambda: tmp_path)
    monkeypatch.setattr(cli, 'ensure_runtime', lambda: (SimpleNamespace(control_url='http://daemon'), True))
    requests = []

    def request(url, path, **kwargs):
        requests.append((path, kwargs))
        return {'application': {'state': 'running'}}

    monkeypatch.setattr(cli, '_request_json', request)
    assert cli.main(['demo', name]) == 0
    assert [path for path, _ in requests] == [
        '/daemon/application/stop', '/daemon/application/select', '/daemon/application/start',
    ]
    selected = Path(requests[1][1]['payload']['application_dir'])
    assert selected.is_relative_to(tmp_path)
    assert (selected / 'web/index.html').is_file()
    assert requests[0][1]['timeout'] == cli.APPLICATION_STOP_TIMEOUT_SECONDS


def test_demo_does_not_start_when_stop_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, 'default_runtime_state_root', lambda: tmp_path)
    monkeypatch.setattr(cli, 'ensure_runtime', lambda: (SimpleNamespace(control_url='http://daemon'), True))
    calls = []

    def fail(url, path, **kwargs):
        calls.append(path)
        raise cli.CliError('stop failed')

    monkeypatch.setattr(cli, '_request_json', fail)
    assert cli.main(['demo', 'sdk-test-bench']) == 2
    assert calls == ['/daemon/application/stop']


def test_preparation_preserves_artifacts_and_omits_developer_files(tmp_path):
    root = bundled_apps.prepare_demo('sdk-test-bench', tmp_path)
    (root / 'artifacts').mkdir()
    photo = root / 'artifacts/photo.jpg'
    photo.write_bytes(b'user photo')
    assert bundled_apps.prepare_demo('sdk-test-bench', tmp_path) == root
    assert photo.read_bytes() == b'user photo'
    assert not (root / '.venv').exists()
    assert not (root / 'tests').exists()
    assert (root / 'assets/sample_speech.wav').is_file()


def test_missing_bundle_fails_before_stopping_current_app(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, 'default_runtime_state_root', lambda: tmp_path)
    monkeypatch.setattr(bundled_apps, '_source_root', lambda directory: tmp_path / 'missing')
    monkeypatch.setattr(cli, 'ensure_runtime', lambda: pytest.fail('must not touch Daemon'))
    assert cli.main(['demo', 'expression-lab']) == 2


def test_unknown_demo_rejected_without_starting_runtime(monkeypatch):
    monkeypatch.setattr(cli, 'ensure_runtime', lambda: pytest.fail('must not touch Daemon'))
    with pytest.raises(SystemExit) as error:
        cli.main(['demo', '../other'])
    assert error.value.code == 2


def test_modified_cache_is_not_overwritten(tmp_path):
    root = bundled_apps.prepare_demo('sdk-test-bench', tmp_path)
    entry = root / 'app.py'
    entry.write_text('# local change', encoding='utf-8')
    with pytest.raises(ValueError, match='cache was modified'):
        bundled_apps.prepare_demo('sdk-test-bench', tmp_path)
    assert entry.read_text(encoding='utf-8') == '# local change'


def test_catalog_covers_runtime_resources_without_development_data():
    import json

    package = Path(bundled_apps.__file__).parent
    catalog = json.loads((package / 'bundled-apps.json').read_text(encoding='utf-8'))
    assert set(catalog) == set(bundled_apps.DEMO_NAMES)
    for spec in catalog.values():
        root = bundled_apps._source_root(spec['directory'])
        selected = set(spec['files'])
        expected = {'app.py', 'service.py', 'app.json', 'README.md', 'icon.svg'}
        for directory in ('web', 'assets', 'firmware'):
            expected.update(p.relative_to(root).as_posix() for p in (root / directory).rglob('*') if p.is_file())
        if spec['directory'] == 'expression_lab':
            expected.add('KUROBLOB_AI_LICENSE.txt')
        assert selected == expected
        assert not any(set(Path(p).parts) & {'.venv', 'artifacts', '__pycache__', 'tests', 'node_modules'} for p in selected)
