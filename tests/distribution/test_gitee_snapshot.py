import subprocess

import pytest

from watcherobot.distribution.gitee_repository import GiteeRepository
from watcherobot.distribution.ports import HubInvalidResponse


def repository(tmp_path):
    root = tmp_path / 'origin'
    root.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()
    git('init')
    (root / 'app.json').write_bytes(b'{}\n')
    (root / 'web').mkdir()
    (root / 'web' / 'asset.bin').write_bytes(bytes(range(256)))
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture')
    return root, git, git('rev-parse', 'HEAD')


def test_snapshot_uses_git_without_api_or_checkout(tmp_path, monkeypatch):
    root, git, sha = repository(tmp_path)
    from watcherobot.distribution.gitee_snapshot import GitSnapshot
    monkeypatch.setattr(GitSnapshot, '_remote_url', staticmethod(lambda repo: root.as_uri()))
    target = tmp_path / 'result'
    target.mkdir()
    hub = GiteeRepository()
    monkeypatch.setattr(hub.api, 'request', lambda *a: pytest.fail('No API expected'))
    hub.download_repository_snapshot(repo_id='owner/app', commit=sha, target=target)
    assert (target / 'web/asset.bin').read_bytes() == bytes(range(256))
    assert (target / 'app.json').read_bytes() == b'{}\n'
    assert not (target / '.git').exists()


def test_git_snapshot_rejects_symlink_before_export(tmp_path, monkeypatch):
    root, git, sha = repository(tmp_path)
    blob = git('hash-object', '-w', 'app.json')
    git('update-index', '--add', '--cacheinfo', f'120000,{blob},link')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'link')
    from watcherobot.distribution.gitee_snapshot import GitSnapshot
    monkeypatch.setattr(GitSnapshot, '_remote_url', staticmethod(lambda repo: root.as_uri()))
    target = tmp_path / 'result'
    target.mkdir()
    with pytest.raises(HubInvalidResponse):
        GiteeRepository().download_repository_snapshot(repo_id='owner/app', commit=git('rev-parse', 'HEAD'), target=target)
    assert not list(target.iterdir())


@pytest.mark.parametrize('name,size', [('CON.txt', 1), ('huge.bin', 1024 * 1024 + 1)])
def test_export_limits_apply_before_read(tmp_path, name, size):
    tree = {'tree': [dict(path=name, size=size, type='blob', mode='100644', sha='a' * 40)]}
    with pytest.raises(HubInvalidResponse):
        GiteeRepository()._export_snapshot('owner/app', 'a' * 40, tmp_path, tree,
                                           lambda **kwargs: pytest.fail('Must not read'))
    assert not list(tmp_path.iterdir())


def test_nonempty_target_is_preserved_without_network(tmp_path, monkeypatch):
    from watcherobot.distribution.gitee_snapshot import GitSnapshot
    monkeypatch.setattr(GitSnapshot, 'open', lambda *a: pytest.fail('No network expected'))
    (tmp_path / 'keep').write_bytes(b'keep')
    with pytest.raises(HubInvalidResponse):
        GiteeRepository().download_repository_snapshot(repo_id='owner/app', commit='a' * 40, target=tmp_path)
    assert (tmp_path / 'keep').read_bytes() == b'keep'
