"""Daemon initialization does not require Creator audio codecs."""

import subprocess
import sys


def test_daemon_initializes_without_loading_audio_codecs() -> None:
    code = '''
import importlib.abc
import sys
class RejectAudioCodecs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "av" or fullname.startswith("av."):
            raise AssertionError("Daemon startup eagerly loaded audio codecs")
sys.meta_path.insert(0, RejectAudioCodecs())
from pathlib import Path
from tempfile import TemporaryDirectory
from watcherobot.runtime.daemon.runtime import DaemonRuntime
with TemporaryDirectory() as root:
    runtime = DaemonRuntime(application_dir=Path(root), current_app=None)
assert "av" not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
