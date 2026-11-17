"""Official release cache; never opens a device connection."""
import hashlib
import io
import wave
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen
import tarfile
import threading
from PIL import Image
from .preview import animation_frames

VERSION = 'v0.0.10'
REPOSITORY = 'orulink-ai/WatcheRobot_sd'
ARCHIVE_SHA256 = '407ef46fd498ccb3461c268de33a30b12897b5c6ddf6868f2bb4a48e6387479a'
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
ALIASES = {**{f'standby_mouth{i}': f'standby{i}' for i in range(1, 7)},
           **{f'standby_little_mouth{i}': f'standby_little{i}' for i in range(1, 5)},
           **{f'{name}_mouth': name for name in ('love', 'happy', 'biyeyes', 'clmness',
                'inquisitive', 'sunglasses', 'hesitate', 'music')},
           'custom1': 'love', 'bigeyes': 'biyeyes', 'calmness': 'clmness'}


class OfficialResources:
    def __init__(self, root: Path | None = None):
        base = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME')
                    or Path.home() / '.cache')
        self.root = root or base / 'Watcher' / 'idle-studio' / 'official'
        self.folder = self.root / VERSION
        self.lock = threading.RLock()
        self.entries = None

    def _archive(self):
        path = self.folder / 'resources.tar.gz'
        if not path.exists():
            self.folder.mkdir(parents=True, exist_ok=True)
            name = f'watche-sd-resources-{VERSION}.tar.gz'
            downloaded = None
            try:
                request = Request(
                    f'https://github.com/{REPOSITORY}/releases/download/{VERSION}/{name}',
                    headers={'User-Agent': 'Watcher-Idle-Studio/0.1.0'},
                )
                digest, size = hashlib.sha256(), 0
                with tempfile.NamedTemporaryFile(dir=self.folder, suffix='.part', delete=False) as output:
                    downloaded = Path(output.name)
                    with urlopen(request, timeout=60) as response:
                        while block := response.read(1024 * 1024):
                            size += len(block)
                            if size > MAX_ARCHIVE_BYTES:
                                raise ValueError('官方资源包大小超出限制')
                            digest.update(block)
                            output.write(block)
                if digest.hexdigest() != ARCHIVE_SHA256:
                    raise ValueError('官方资源包校验失败，请重试')
                downloaded.replace(path)
            except OSError as exc:
                raise ValueError('官方资源同步失败，请检查网络后重试') from exc
            finally:
                if downloaded is not None:
                    downloaded.unlink(missing_ok=True)
        return path

    def _read(self, path, limit):
        if not isinstance(path, str) or '..' in path or path.startswith('/'):
            raise ValueError('资源路径无效')
        with tarfile.open(self._archive(), 'r:gz') as archive:
            member = archive.getmember(path)
            if not member.isfile() or not 0 < member.size <= limit:
                raise ValueError('官方资源大小或类型无效')
            return archive.extractfile(member).read()

    def resolve(self, resource, sha256=None):
        with self.lock:
            if self.entries is None:
                self.entries = json.loads(self._read('official_catalog.json', 1024 * 1024))['expressions']
            entries = self.entries
            if sha256:
                entry = next((e for e in entries if e.get('assets', {}).get('animation', {}).get('sha256') == sha256), None)
                if entry is None:
                    raise ValueError('设备动画与官方版本不一致，暂不能用官方动画预览')
                match = 'sha256'
            else:
                target = ALIASES.get(resource, resource)
                entry = next((e for e in entries if e['id'] == target), None)
                match = 'legacy_alias' if target != resource else 'name'
            if entry is None:
                raise ValueError('官方资源中没有此表情；自定义素材暂不提供官方预览')
            digest = entry['assets']['animation']['sha256']
            if not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('资源摘要无效')
            return {'resource': entry['id'], 'sha256': digest, 'matched_by': match,
                    'version': VERSION, 'path': f'assets/anim/{digest}.animpack'}

    def animation(self, resolved):
        data = self._read(resolved['path'], 16 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != resolved['sha256']:
            raise ValueError('官方动画校验失败')
        return data

    def companion(self, resource, kind, sha256=None):
        if kind not in ('action', 'sound'):
            raise ValueError('素材类型无效')
        with self.lock:
            if self.entries is None:
                self.entries = json.loads(self._read('official_catalog.json', 1024 * 1024))['expressions']
            target = ALIASES.get(resource, resource)
            entry = next((e for e in self.entries if
                (e.get('assets', {}).get(kind, {}).get('sha256') == sha256 if sha256 else e['id'] == target)), None)
            asset = entry.get('assets', {}).get(kind) if entry else None
            if not asset:
                raise ValueError('官方资源中没有匹配的动作或声音：' + resource)
            digest = asset['sha256']
            if not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('素材摘要无效')
            folder, suffix = ('actions', 'json') if kind == 'action' else ('sfx', 'pcm')
            data = self._read(f'assets/{folder}/{digest}.{suffix}', 16 * 1024 * 1024)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('官方素材校验失败')
            if kind == 'action':
                return json.loads(data)
            if len(data) % 2:
                raise ValueError('声音格式无效')
            output = io.BytesIO()
            with wave.open(output, 'wb') as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(data)
            return output.getvalue()

    def preview(self, resource, sha256=None):
        with self.lock:
            resolved = self.resolve(resource, sha256)
            digest = resolved['sha256']
            metadata = self.folder / (digest + '.frames-v2.json')
            sheet_path = self.folder / (digest + '.png')
            if not metadata.exists() or not sheet_path.exists():
                frames, durations = animation_frames(self.animation(resolved))
                width, height = frames[0].size
                columns = 8
                sheet = Image.new('RGB', (width * columns, height * ((len(frames) + columns - 1) // columns)))
                for i, frame in enumerate(frames):
                    sheet.paste(frame, ((i % columns) * width, (i // columns) * height))
                sheet.save(sheet_path)
                metadata.write_text(json.dumps({'width': width, 'height': height, 'columns': columns,
                    'durations': durations}), encoding='utf-8')
            return {**resolved, **json.loads(metadata.read_text(encoding='utf-8')),
                    'url': '/api/official/sheet/' + digest}

    def sheet(self, digest):
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('资源摘要无效')
        return self.folder / (digest + '.png')
