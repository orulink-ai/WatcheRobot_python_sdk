"""Shared implementation for run.ps1 and run.sh."""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description='First Meeting SDK application')
    parser.add_argument('--test', '-Test', action='store_true', help='Run application tests instead of starting')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    launcher_config = root / 'artifacts' / 'launcher.json'
    if launcher_config.exists():
        executable = Path(json.loads(launcher_config.read_text(encoding='utf-8'))['python'])
        if executable.resolve() != Path(sys.executable).resolve():
            return subprocess.call([str(executable), str(root / 'launch.py'), *sys.argv[1:]])
    if args.test:
        import pytest
        return pytest.main([str(root / 'tests'), '-q'])
    from watcherobot.cli import main as sdk_main
    return sdk_main(['app', 'run', str(root)])


if __name__ == '__main__':
    raise SystemExit(main())
