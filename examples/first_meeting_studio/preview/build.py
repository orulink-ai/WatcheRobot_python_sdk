"""Build self-contained preview; Pillow required. Assets never include user photos."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import zlib
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent

def uri(data):
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')

def import_frames(firmware):
    source = firmware / 'firmware/s3/components/services/anim_service/src'
    provenance = {}
    for name, count in [('photo', 35), ('processing', 40)]:
        path = source / f'watcher_{name}_asset.c'
        text = path.read_text(encoding='utf-8')
        offsets = [int(n) for n in re.findall(r'(\d+)U?', re.search(r'offsets\[.*?\]\s*=\s*\{(.*?)\}', text, re.S).group(1))]
        data = bytes(int(h, 16) for h in re.findall(r'0x([0-9a-fA-F]{2})', text.split(f'watcher_{name}_zlib')[1]))
        assert len(offsets) == count + 1
        folder = HERE / 'assets' / name
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            raw = zlib.decompress(data[offsets[i]:offsets[i+1]])
            assert len(raw) == 412 * 412 * 2
            rgb = bytearray()
            for j in range(0, len(raw), 2):
                value = (raw[j] << 8) | raw[j+1]
                r, g, b = (value >> 11) & 31, (value >> 5) & 63, value & 31
                rgb.extend(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
            Image.frombytes('RGB', (412, 412), bytes(rgb)).save(folder / f'{i:02d}.png', optimize=True)
        provenance[name] = {'source': str(path.relative_to(firmware)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'count': count, 'format': 'RGB565 big-endian, 412x412'}
    (HERE / 'assets' / 'provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')

def build(output):
    assets = {name: uri((HERE.parent / 'assets/provisioning' / f'{name}.png').read_bytes()) for name in ['scan', 'network', 'failed']}
    for name, count in [('photo', 35), ('processing', 40)]:
        assets[name] = [uri((HERE / 'assets' / name / f'{i:02d}.png').read_bytes()) for i in range(count)]
    card = Image.new('RGB', (412, 412), '#e6ebda')
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((70, 76, 342, 320), radius=48, fill='#21473d')
    d.ellipse((117, 148, 149, 180), fill='#dbeab3')
    d.ellipse((263, 148, 295, 180), fill='#dbeab3')
    d.arc((173, 180, 239, 233), 0, 180, fill='#dbeab3', width=6)
    d.line((70, 350, 342, 350), fill='#b7c7a1', width=3)
    buffer = io.BytesIO()
    card.save(buffer, format='PNG')
    assets['souvenir'] = uri(buffer.getvalue())
    html = (HERE / 'template.html').read_text(encoding='utf-8')
    for marker, text in [('ASSETS', 'const ASSETS=' + json.dumps(assets) + ';'), ('FLOW', (HERE / 'flow.js').read_text(encoding='utf-8')), ('APP', (HERE / 'app.js').read_text(encoding='utf-8'))]:
        html = html.replace('/*__' + marker + '__*/', text)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding='utf-8')
    print(f'Built {output.name}: {output.stat().st_size} bytes')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--firmware', type=Path, help='Optional firmware source to refresh embedded frames')
    parser.add_argument('--output', type=Path, default=HERE / 'Watcher-初见体验.html')
    args = parser.parse_args()
    if args.firmware:
        import_frames(args.firmware)
    build(args.output)
