"""Anonymous fixed-revision Git object reads, without checkout or API calls."""
from __future__ import annotations

import os
import hashlib
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .ports import CatalogDocument, HubFileNotFound, HubInvalidResponse, HubNetworkError


class GitSnapshot:
    @staticmethod
    def _remote_url(repo_id: str) -> str:
        return f'https://gitee.com/{repo_id}.git'

    @contextmanager
    def open(self, repo_id: str, commit: str | None) -> Iterator[tuple[dict[str, Any], Any]]:
        with tempfile.TemporaryDirectory(prefix='watcher-gitee-objects-') as temp:
            root = Path(temp)
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1', GIT_ASKPASS='')

            def run(*args: str) -> bytes:
                try:
                    result = subprocess.run(
                        ['git', '-c', 'credential.helper=', '-c', 'http.followRedirects=false',
                         '-c', 'core.askPass=',
                         '-c', f'core.hooksPath={os.devnull}', '-c', 'fetch.fsckObjects=true',
                         '-c', 'protocol.ext.allow=never', *args],
                        cwd=root, env=env, capture_output=True, timeout=180, check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    raise HubNetworkError('Git 不可用或下载超时，请安装 Git 后重试') from None
                if result.returncode:
                    raise HubNetworkError('Gitee Git 下载失败，请检查公开访问、网络和指定版本；不会自动切换凭据')
                return result.stdout

            run('init', '--bare', '--template=')
            run('fetch', '--depth=1', '--no-tags', '--no-recurse-submodules',
                self._remote_url(repo_id), commit or 'HEAD')
            resolved = run('rev-parse', '--verify', 'FETCH_HEAD^{commit}').decode().strip()
            if commit is not None and resolved != commit:
                raise HubInvalidResponse('Git 未返回指定的固定版本')
            commit = resolved
            records = run('ls-tree', '-r', '-t', '-l', '-z', commit).split(b'\0')
            tree: list[dict[str, Any]] = []
            for record in records:
                if not record:
                    continue
                try:
                    metadata, path = record.split(b'\t', 1)
                    mode, kind, sha, size = metadata.split()
                    tree.append(dict(path=path.decode('utf-8'), mode=mode.decode(),
                                     type=kind.decode(), sha=sha.decode(),
                                     size=int(size) if size != b'-' else None))
                except (ValueError, UnicodeError):
                    raise HubInvalidResponse('Git 文件树格式无效') from None
            blobs = {item['path']: item for item in tree if item['type'] == 'blob'}

            def read_file(*, repo_id: str, commit: str, path: str) -> bytes:
                return run('cat-file', 'blob', blobs[path]['sha'])

            yield {'tree': tree, 'commit': resolved}, read_file

    def read_catalog(self, repo_id: str, path: str, commit: str | None = None) -> CatalogDocument:
        with self.open(repo_id, commit) as (tree, read_file):
            matches = [item for item in tree['tree'] if item['path'] == path]
            if len(matches) != 1:
                raise HubFileNotFound('Gitee 应用目录文件不存在')
            item = matches[0]
            if (item['type'] != 'blob' or item['mode'] not in ('100644', '100755')
                    or type(item['size']) is not int or not 0 <= item['size'] <= 1024 * 1024):
                raise HubInvalidResponse('Gitee 应用目录文件类型或大小无效')
            data = read_file(repo_id=repo_id, commit=tree['commit'], path=path)
            digest = hashlib.sha1(f'blob {len(data)}\0'.encode() + data, usedforsecurity=False).hexdigest()
            if len(data) != item['size'] or digest != item['sha']:
                raise HubInvalidResponse('Gitee 应用目录文件校验失败')
            return CatalogDocument(content=data, commit=tree['commit'])
