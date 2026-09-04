"""Both developer entrypoints must preserve arguments and exit status."""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('platform', ['powershell', 'shell'])
def test_entrypoint_preserves_arguments_and_failure_status(tmp_path, platform):
    root = Path(__file__).parents[1]
    if platform == 'powershell':
        runtime = shutil.which('pwsh') or shutil.which('powershell')
        command = [runtime, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File']
        entry = 'run.ps1'
    else:
        git_bash = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe'
        runtime = str(git_bash) if os.name == 'nt' and git_bash.exists() else shutil.which('bash')
        command = [runtime]
        entry = 'run.sh'
    if not runtime:
        pytest.skip(f'{platform} is not installed on this host')
    shutil.copyfile(root / entry, tmp_path / entry)
    (tmp_path / 'launch.py').write_text(
        'import json,os,sys\nprint(json.dumps(sys.argv[1:]))\n'
        'raise SystemExit(int(os.environ["ENTRYPOINT_TEST_EXIT"]))\n', encoding='utf-8')
    # Git Bash on Windows does not necessarily provide python3. Use the same
    # test interpreter, without relying on Windows Store aliases or a download.
    shim = tmp_path / 'python3'
    shim.write_text('#!/bin/sh\nexec ' + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"\n', encoding='utf-8', newline='\n')
    shim.chmod(0o755)
    env = os.environ.copy()
    env['PATH'] = os.pathsep.join([str(tmp_path), str(Path(sys.executable).parent), env.get('PATH', '')])
    arguments = ['--sample', 'value with spaces', '中文']
    for expected in (0, 7):
        env['ENTRYPOINT_TEST_EXIT'] = str(expected)
        result = subprocess.run(command + [(tmp_path / entry).as_posix(), *arguments], env=env,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
        assert result.returncode == expected, result.stderr
        assert json.loads(result.stdout) == arguments
