"""Exercise the actual CLI protocol with legacy Windows stream encodings."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("encoding", ["gbk", "cp1252", "utf-8"])
@pytest.mark.parametrize("entry", ["watcherobot.runtime.daemon", "watcherobot.runtime.frozen_entry"])
def test_prepare_bundle_returns_utf8_path(tmp_path: Path, encoding: str, entry: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "watcher-runtime.exe").write_bytes(b"publication fixture")
    instance = tmp_path / "中文 日本語 café 空格 🤖"
    env = {
        **os.environ,
        "PYTHONIOENCODING": encoding,
        "PYTHONUTF8": "0",
        "WATCHER_RUNTIME_INSTANCE_ROOT": str(instance),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-m", entry, "--prepare-bundle", str(source)],
        env=env, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    published = Path(result.stdout.decode("utf-8").rstrip("\r\n"))
    assert published.is_absolute()
    assert (published / "watcher-runtime.exe").read_bytes() == b"publication fixture"
    assert result.stderr == b""


@pytest.mark.parametrize("entry", ["watcherobot.runtime.daemon", "watcherobot.runtime.frozen_entry"])
def test_argument_errors_are_utf8(entry: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", entry, "--未知参数🤖"],
        env={**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0",
             "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        capture_output=True, timeout=30,
    )
    assert result.returncode == 2
    assert "--未知参数🤖" in result.stderr.decode("utf-8")
