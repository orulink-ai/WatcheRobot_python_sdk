import hashlib
import io
import json
import tarfile

import pytest
from watcherobot.idle_studio.official import OfficialResources


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return output.getvalue()


def install(tmp_path, data=b'animation'):
    digest = hashlib.sha256(data).hexdigest()
    catalog = {'expressions': [{'id': 'standby1', 'assets': {'animation': {
        'sha256': digest, 'path': 'assets/anim/' + digest + '.animpack'}}}]}
    payload = archive({'official_catalog.json': json.dumps(catalog).encode(),
                       'assets/anim/' + digest + '.animpack': data})
    target = tmp_path / 'v0.0.10'
    target.mkdir()
    (target / 'resources.tar.gz').write_bytes(payload)
    return OfficialResources(tmp_path), digest


def test_exact_and_explicit_legacy_names_resolve_without_usb(tmp_path):
    resources, digest = install(tmp_path)
    assert resources.resolve('standby1')['sha256'] == digest
    result = resources.resolve('standby_mouth1')
    assert result['sha256'] == digest
    assert result['matched_by'] == 'legacy_alias'
    with pytest.raises(ValueError, match='官方资源'):
        resources.resolve('my_standby1')


def test_content_identity_takes_priority_and_rejects_mismatch(tmp_path):
    resources, digest = install(tmp_path)
    assert resources.resolve('renamed', digest)['sha256'] == digest
    with pytest.raises(ValueError, match='版本'):
        resources.resolve('standby1', '0' * 64)


def test_cached_archive_works_without_network_and_checks_asset_hash(tmp_path):
    resources, digest = install(tmp_path)
    assert resources.animation(resources.resolve('standby1')) == b'animation'
    with pytest.raises(ValueError):
        resources.animation({'path': '../../secret', 'sha256': digest})


def test_official_preview_route_never_calls_device(tmp_path):
    from fastapi.testclient import TestClient
    from watcherobot.idle_studio.server import create_app
    class Official:
        def preview(self, resource, sha256=None):
            return {'resource': resource, 'version': 'v0.0.10'}
    client = TestClient(create_app(object(), official=Official()))
    result = client.get('/api/official/preview?resource=standby1')
    assert result.status_code == 200
    assert result.json()['resource'] == 'standby1'


def test_sprite_preserves_duplicate_frames_colors_and_timing(tmp_path):
    import struct
    from PIL import Image
    frames = [bytes.fromhex('f80007e0'), bytes.fromhex('f80007e0'), bytes.fromhex('001fffff')]
    header = struct.pack('<4sHHHHBBHIII', b'ANPK', 2, 2, 1, 3, 0, 0, 100, 28, 64, 4)
    descriptors = b''.join(struct.pack('<IIHH', i * 4, 4, delay, 0)
                           for i, delay in enumerate([30, 70, 120]))
    resources, digest = install(tmp_path, header + descriptors + b''.join(frames))
    result = resources.preview('standby1')
    assert result['durations'] == [30, 70, 120]
    with Image.open(resources.sheet(digest)) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert image.getpixel((2, 0)) == (255, 0, 0)
        assert image.getpixel((4, 0)) == (0, 0, 255)
    # Warm playback must reuse the decoded cache, even if decoding is unavailable.
    resources.animation = lambda _: pytest.fail('cached preview decoded again')
    assert resources.preview('standby1')['durations'] == [30, 70, 120]


def test_corrupt_content_is_not_played(tmp_path):
    resources, digest = install(tmp_path)
    result = resources.resolve('standby1')
    result['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='校验'):
        resources.animation(result)

def test_companion_assets_validate_hash_and_wrap_pcm(tmp_path):
    import wave
    action = json.dumps({'fps': 10, 'frame_start': 0, 'frame_end': 9, 'animated_objects': []}).encode()
    pcm = b'AB' * 240
    assets = {}
    members = {}
    for kind, data, folder, suffix in [('action', action, 'actions', 'json'), ('sound', pcm, 'sfx', 'pcm')]:
        digest = hashlib.sha256(data).hexdigest()
        assets[kind] = {'sha256': digest}
        members[f'assets/{folder}/{digest}.{suffix}'] = data
    members['official_catalog.json'] = json.dumps({'expressions': [{'id': 'standby1', 'assets': assets}]}).encode()
    folder = tmp_path / 'v0.0.10'
    folder.mkdir()
    (folder / 'resources.tar.gz').write_bytes(archive(members))
    resources = OfficialResources(tmp_path)
    assert resources.companion('standby_mouth1', 'action')['fps'] == 10
    result = resources.companion('standby1', 'sound')
    with wave.open(io.BytesIO(result), 'rb') as audio:
        assert audio.getframerate() == 24000
        assert audio.getnframes() == 240
        assert audio.readframes(240) == pcm
    with pytest.raises(ValueError):
        resources.companion('unknown', 'sound')
