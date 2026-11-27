"""Export six character-selection portraits and a backdrop from a locally owned game.

Usage: python tools/export_ui_art.py <SlayTheSpire2.pck>
Requires Pillow. Assets retain the game's copyright; see static/assets/NOTICE.md.
Only reads an unencrypted PCK and writes the fixed UI asset names in this repository.
"""
import argparse
import io
import json
import struct
from pathlib import Path

from PIL import Image


def directory(stream):
    magic, version, _, _, _, flags = struct.unpack('<4s5I', stream.read(24))
    if magic != b'GDPC' or version not in (2, 3) or flags & 1:
        raise ValueError('Unsupported or encrypted game pack')
    base = struct.unpack('<Q', stream.read(8))[0]
    if version == 3:
        offset = struct.unpack('<Q', stream.read(8))[0]
        stream.seek(offset)
    else:
        stream.read(64)
    count = struct.unpack('<I', stream.read(4))[0]
    for _ in range(count):
        length = struct.unpack('<I', stream.read(4))[0]
        name = stream.read(length).rstrip(b'\0').decode('utf-8')
        offset, size = struct.unpack('<QQ', stream.read(16))
        stream.read(16)
        entry_flags = struct.unpack('<I', stream.read(4))[0]
        yield name, offset + (base if flags & 2 else 0), size, entry_flags


def decode(data):
    if data[:4] != b'GST2':
        raise ValueError('Expected a Godot compressed texture')
    original = struct.unpack_from('<II', data, 8)
    width, height = struct.unpack_from('<HH', data, 40)
    fmt = struct.unpack_from('<I', data, 48)[0]
    formats = {17: 1, 18: 2, 19: 3, 22: 7}
    if fmt in formats:
        image = Image.frombytes('RGBA', (width, height), data[52:], 'bcn', (formats[fmt],))
    else:
        offset = next(i for i in range(36, 80) if data[i:i+4] in (b'RIFF', b'\x89PNG'))
        image = Image.open(io.BytesIO(data[offset:])).convert('RGBA')
    return image.crop((0, 0, *original))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pack', type=Path)
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / 'static' / 'assets'
    output.mkdir(parents=True, exist_ok=True)
    names = {f'char_select_{c}': c for c in ('ironclad', 'silent', 'defect', 'regent', 'necrobinder', 'fool')}
    names['silent1_epoch'] = 'spire-backdrop'
    provenance = {}
    with args.pack.open('rb') as stream:
        entries = list(directory(stream))
        for name, offset, size, flags in entries:
            key = name.split('/')[-1].split('.png')[0]
            if not name.startswith('.godot/imported/') or key not in names:
                continue
            if flags & 1:
                raise ValueError('Encrypted texture')
            stream.seek(offset)
            image = decode(stream.read(size))
            target = names[key] + '.webp'
            image.save(output / target, quality=92, method=6)
            provenance[target] = name
    if len(provenance) != len(names):
        raise ValueError('Missing expected character art')
    (output / 'sources.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    print(f'Exported {len(provenance)} UI images')


if __name__ == '__main__':
    main()
