import pytest

from watcherobot.distribution.cli import build_parser
from watcherobot.distribution.credentials import SystemCredentialStore
from watcherobot.distribution.gitee_auth import GiteeHubClient
from watcherobot.distribution.hub_http import JsonResponse
from watcherobot.distribution.ports import AccessToken, HubAuthenticationError, HubInvalidResponse
from tests.distribution.test_credentials import FakeKeyring
from watcherobot.distribution import cli
from watcherobot.distribution.ports import HubIdentity


def test_credentials_are_isolated_by_provider():
    backend = FakeKeyring()
    hf = SystemCredentialStore(provider='huggingface', backend=backend)
    gitee = SystemCredentialStore(provider='gitee', backend=backend)
    hf.save(AccessToken('hf-test'))
    gitee.save(AccessToken('gitee-test'))
    gitee.delete()
    assert hf.load() == AccessToken('hf-test')
    assert gitee.load() is None


@pytest.mark.parametrize('command', ['login', 'logout'])
def test_auth_requires_explicit_provider(command):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['app', command])
    args = build_parser().parse_args(['app', command, '--provider', 'gitee'])
    assert args.provider == 'gitee'


class Transport:
    def __init__(self, response):
        self.response = response

    def get_json(self, url, headers, *, timeout):
        assert url == 'https://gitee.com/api/v5/user'
        assert headers['Authorization'] == 'Bearer test-secret'
        return self.response


def test_gitee_identity():
    client = GiteeHubClient(transport=Transport(JsonResponse(200, {'login': 'developer', 'name': 'Dev'})))
    assert client.whoami(AccessToken('test-secret')).username == 'developer'


@pytest.mark.parametrize('status,payload,error', [
    (401, {'message': 'test-secret'}, HubAuthenticationError),
    (200, {'name': 'Dev'}, HubInvalidResponse),
    (200, {'login': '../bad'}, HubInvalidResponse),
])
def test_gitee_rejects_invalid_identity(status, payload, error):
    client = GiteeHubClient(transport=Transport(JsonResponse(status, payload)))
    with pytest.raises(error) as exc:
        client.whoami(AccessToken('test-secret'))
    assert 'test-secret' not in str(exc.value)


def test_gitee_login_validates_then_saves_and_logout_is_isolated(monkeypatch, capsys):
    backend = FakeKeyring()
    hf = SystemCredentialStore(provider='huggingface', backend=backend)
    hf.save(AccessToken('hf-existing'))
    monkeypatch.setattr(cli, 'SystemCredentialStore', lambda **kw: SystemCredentialStore(backend=backend, **kw))
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: 'gitee-test-secret')
    class Hub:
        def whoami(self, token):
            assert token.value == 'gitee-test-secret'
            return HubIdentity('developer')
    monkeypatch.setattr(cli, 'GiteeHubClient', Hub)
    assert cli.main(['app', 'login', '--provider', 'gitee']) == 0
    assert SystemCredentialStore(provider='gitee', backend=backend).load() == AccessToken('gitee-test-secret')
    assert cli.main(['app', 'login', '--provider', 'gitee', '--status', '--jsonl']) == 0
    assert cli.main(['app', 'logout', '--provider', 'gitee']) == 0
    assert hf.load() == AccessToken('hf-existing')
    assert 'gitee-test-secret' not in capsys.readouterr().out


def test_failed_force_login_preserves_existing_credential(monkeypatch):
    backend = FakeKeyring()
    store = SystemCredentialStore(provider='gitee', backend=backend)
    store.save(AccessToken('old-token'))
    monkeypatch.setattr(cli, 'SystemCredentialStore', lambda **kw: store)
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: 'invalid-token')
    class Hub:
        def whoami(self, token):
            raise HubAuthenticationError('Invalid token')
    monkeypatch.setattr(cli, 'GiteeHubClient', Hub)
    assert cli.main(['app', 'login', '--provider', 'gitee', '--force']) == 3
    assert store.load() == AccessToken('old-token')


def test_jsonl_missing_login_never_prompts(monkeypatch):
    backend = FakeKeyring()
    monkeypatch.setattr(cli, 'SystemCredentialStore', lambda **kw: SystemCredentialStore(backend=backend, **kw))
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: pytest.fail('must not prompt'))
    assert cli.main(['app', 'login', '--provider', 'gitee', '--jsonl']) == 3
