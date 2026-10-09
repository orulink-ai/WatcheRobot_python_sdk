"""Materialize bundled demos outside the read-only Python installation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from watcherobot.runtime.daemon.application.manifest import ApplicationManifest


DEMO_NAMES = ('sdk-test-bench', 'expression-lab')


def _source_root(directory: str) -> Path:
    package = Path(__file__).resolve().parent
    bundled = package / '_bundled_apps' / directory
    if bundled.is_dir():
        return bundled
    # Editable source installs use the same catalog as release wheels.
    repository = package.parent.parent
    if (repository / 'pyproject.toml').is_file():
        return repository / 'examples' / directory
    return bundled


def prepare_demo(name: str, state_root: Path) -> Path:
    """Validate before lifecycle changes; preserve per-content runtime artifacts."""
    if name not in DEMO_NAMES:
        raise ValueError(f'Unknown bundled demo: {name}')
    catalog: dict[str, Any] = json.loads(
        Path(__file__).with_name('bundled-apps.json').read_text(encoding='utf-8')
    )
    spec = catalog[name]
    source = _source_root(spec['directory'])
    files: dict[str, bytes] = {}
    digest = hashlib.sha256()
    for relative in spec['files']:
        path = Path(relative)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid bundled demo resource path')
        data = (source / path).read_bytes()
        files[relative] = data
        digest.update(relative.encode('utf-8') + b'\0')
        digest.update(hashlib.sha256(data).digest())
    manifest = ApplicationManifest.load(source)
    if manifest.app_id != spec['app_id']:
        raise ValueError('Bundled demo Application ID mismatch')
    parent = state_root / 'bundled-demos' / name
    # Keep room for resource names under Windows' traditional path limit.
    destination = parent / digest.hexdigest()[:20]
    parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with tempfile.TemporaryDirectory(prefix='.prepare-', dir=parent) as temporary:
            staging = Path(temporary) / 'source'
            staging.mkdir()
            for relative, data in files.items():
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            try:
                staging.rename(destination)
            except OSError:
                if not destination.is_dir():
                    raise
                # Another invocation may have prepared the identical snapshot.
    for relative, data in files.items():
        if (destination / relative).read_bytes() != data:
            raise ValueError(f'Bundled demo cache was modified: {destination}')
    return destination
