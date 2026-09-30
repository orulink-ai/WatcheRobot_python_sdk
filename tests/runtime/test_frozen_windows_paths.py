"""Load a real native extension beyond MAX_PATH without changing OS policy."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from watcherobot.runtime.windows_paths import _extended_path, _FrozenNativePathFinder, native_executable_options


@pytest.mark.parametrize("source, expected", [
    (r"C:\用户\runtime", r"\\?\C:\用户\runtime"),
    (r"\\server\share\runtime", r"\\?\UNC\server\share\runtime"),
    (r"\\?\C:\用户\runtime", r"\\?\C:\用户\runtime"),
])
def test_extended_paths_preserve_drive_unc_and_existing_prefix(source: str, expected: str) -> None:
    assert _extended_path(source) == expected


def test_native_finder_does_not_search_outside_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args, **kwargs):
        raise AssertionError("must not search outside frozen bundle")

    monkeypatch.setattr("importlib.machinery.FileFinder", unexpected)
    finder = _FrozenNativePathFinder(r"C:\bundle")
    assert finder.find_spec("extension", [r"C:\bundle-other", r"D:\bundle", r"C:\bundle\..\outside"]) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows extended paths")
def test_native_finder_does_not_shadow_frozen_python_packages(tmp_path: Path) -> None:
    (tmp_path / "package").mkdir()
    finder = _FrozenNativePathFinder(str(tmp_path))
    assert finder.find_spec("package") is None


@pytest.mark.skipif(os.name != "nt", reason="Windows native extension loader")
def test_frozen_native_extension_loads_from_long_unicode_path(tmp_path: Path) -> None:
    root = tmp_path / ("long-" * 16) / ("path-" * 16) / "中文用户"
    extended = Path("\\\\?\\" + str(root))
    extended.mkdir(parents=True)
    spec = importlib.util.find_spec("_socket")
    assert spec is not None and spec.origin is not None
    shutil.copyfile(spec.origin, extended / Path(spec.origin).name)
    assert len(str(root / Path(spec.origin).name)) > 260
    program = """
import sys
from watcherobot.runtime.windows_paths import configure_frozen_native_paths
sys.frozen = True
sys._MEIPASS = sys.argv[1]
configure_frozen_native_paths()
configure_frozen_native_paths()
sys.path[:] = [sys.argv[1]]
sys.modules.pop('_socket', None)
import _socket
assert _socket.__file__.startswith('\\\\\\\\?\\\\'), _socket.__file__
_socket.socket().close()
print('native extension loaded')
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(root)], capture_output=True, timeout=30,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess")
def test_executable_beyond_max_path_starts_without_shell(tmp_path: Path) -> None:
    root = tmp_path / ("long-" * 16) / ("path-" * 16) / "中文用户"
    extended = Path("\\\\?\\" + str(root))
    extended.mkdir(parents=True)
    shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32/cmd.exe", extended / "probe.exe")
    command = [str(root / "probe.exe"), "/d", "/c", "exit", "0"]
    assert len(command[0]) > 260
    result = subprocess.run(command, **native_executable_options(command), capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_bare_executable_preserves_path_search() -> None:
    assert native_executable_options(["python", "-m", "watcherobot.runtime.daemon"]) == {}
