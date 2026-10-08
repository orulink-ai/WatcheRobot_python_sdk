"""The optional voice stack must not enter core SDK/Daemon import paths."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize('module', [
    'watcherobot.robot',
    'watcherobot.application',
    'watcherobot.runtime.daemon.runtime',
    'watcherobot.cli',
])
def test_core_import_does_not_load_voice_or_cloud_providers(module):
    result = subprocess.run(
        [sys.executable, '-c',
         'import importlib, sys; importlib.import_module(sys.argv[1]); '
         'assert not any(name.startswith("watcherobot.voice") for name in sys.modules)',
         module],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
