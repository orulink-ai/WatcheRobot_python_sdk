import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'delivery' / 'setup.py'
spec = importlib.util.spec_from_file_location('bundle_setup', SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def test_bundle_verification_rejects_tampering_and_escape(tmp_path):
    data = b'firmware sample'
    (tmp_path / 'firmware.bin').write_bytes(data)
    manifest = {'sha256': {'firmware.bin': hashlib.sha256(data).hexdigest()}}
    (tmp_path / 'bundle.json').write_text(json.dumps(manifest))
    assert setup.verify(tmp_path) == manifest
    (tmp_path / 'firmware.bin').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Checksum mismatch'):
        setup.verify(tmp_path)
    manifest['sha256'] = {'../escape': 'x'}
    (tmp_path / 'bundle.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Invalid bundle path'):
        setup.verify(tmp_path)


def test_both_platform_entrypoints_delegate_arguments():
    assert '@args' in SCRIPT.with_suffix('.ps1').read_text()
    assert '"$@"' in SCRIPT.with_suffix('.sh').read_text()
    assert 'setup.py' in SCRIPT.with_suffix('.ps1').read_text()
    assert 'setup.py' in SCRIPT.with_suffix('.sh').read_text()
