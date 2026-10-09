import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('entrypoint', ['probe.ps1', 'probe.sh'])
def test_platform_entrypoints_forward_help_without_connecting(entrypoint):
    shell = shutil.which('pwsh') or shutil.which('powershell') if entrypoint.endswith('ps1') else shutil.which('sh')
    if not shell:
        pytest.skip('Platform shell is not installed')
    command = ([shell, '-NoProfile', '-File'] if entrypoint.endswith('ps1') else [shell])
    environment = {**os.environ, 'WATCHER_VOICE_PYTHON': sys.executable}
    result = subprocess.run([*command, str(ROOT / entrypoint), '--help'],
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'Probe actual Codex realtime' in result.stdout
