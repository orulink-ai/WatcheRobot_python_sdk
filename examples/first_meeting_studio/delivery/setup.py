"""Verify/install the companion bundle without replacing desktop runtimes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def verify(root):
    manifest = json.loads((root / 'bundle.json').read_text(encoding='utf-8'))
    for name, expected in manifest['sha256'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError('Invalid bundle path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Checksum mismatch: ' + name)
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('verify', 'install', 'run', 'flash'))
    parser.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--port', help='Explicit ESP32-S3 port; required for flash')
    args = parser.parse_args()
    root = args.bundle.resolve()
    manifest = verify(root)
    env = root / '.venv'
    python = env / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    cli = env / ('Scripts/watcherobot.exe' if sys.platform == 'win32' else 'bin/watcherobot')
    if args.action == 'install':
        if not (3, 10) <= sys.version_info[:2] < (3, 13):
            raise ValueError('Python 3.10–3.12 required')
        subprocess.run([sys.executable, '-m', 'venv', str(env)], check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', str(root / manifest['sdk']), '-r', str(root / 'requirements.lock')], check=True)
        subprocess.run([str(cli), 'app', 'check', str(root / 'application')], check=True)
    elif args.action == 'run':
        subprocess.run([str(cli), 'app', 'run', str(root / 'application')], check=True)
    elif args.action == 'flash':
        if not args.port:
            parser.error('flash requires --port; close serial monitors first')
        command = [str(python), '-m', 'esptool', '--chip', 'esp32s3', '--port', args.port,
                   '--baud', '460800', 'write_flash', '--flash_mode', 'dio',
                   '--flash_size', '32MB', '--flash_freq', '80m']
        for offset, name in manifest['firmware']['flash_files'].items():
            command.extend([offset, str(root / name)])
        subprocess.run(command, check=True)
    print('Bundle ' + args.action + ' completed')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
