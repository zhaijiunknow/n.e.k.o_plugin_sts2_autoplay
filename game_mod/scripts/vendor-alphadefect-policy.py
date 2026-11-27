#!/usr/bin/env python3
"""Sync the inference subset and pinned catalogs; never replace published checkpoints.

Use the same fixed checkout/--archive as vendor-alphadefect.py. Dry run by default.
The Python/torch wheels and each model's training catalog remain pinned unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from alphadefect_source import UpstreamSource

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'game-v0.114.0/'
FILES = ('sts2env/__init__.py', 'sts2env/gamever.py', 'sts2env/protocol.py',
         'sts2rl/__init__.py', 'sts2rl/catalog.py', 'sts2rl/content.py',
         'sts2rl/features.py', 'sts2rl/model.py')


def patch_paths(rel: str, text: str) -> str:
    if rel.endswith('/__init__.py'):
        # Upstream sts2env imports the process-driving env module, intentionally absent here.
        package = rel.split('/')[0]
        return f'"""Inference subset of AlphaDefect {package}. See ../README.md."""\n'
    if rel in ('sts2env/gamever.py', 'sts2rl/catalog.py'):
        old = 'REPO = Path(__file__).resolve().parents[2]'
        if text.count(old) != 1:
            raise ValueError(f'{rel}: runtime-root patch no longer matches')
        text = text.replace(old, 'REPO = Path(__import__("os").environ["STS2_POLICY_ROOT"])')
    if rel == 'sts2env/gamever.py':
        old = 'def game_version() -> str:\n'
        if text.count(old) != 1:
            raise ValueError('game_version patch no longer matches')
        text = text.replace(old, old + '    if __import__("os").environ.get("STS2_POLICY_GAME_VERSION"):\n'
                            '        return __import__("os").environ["STS2_POLICY_GAME_VERSION"]\n')
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--upstream', type=Path, default=Path('D:/NekoClaw/_refs/AlphaDefect-Public'))
    ap.add_argument('--archive', type=Path, help='Official GitHub ZIP for an immutable commit')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    source = UpstreamSource(a.upstream, a.archive)
    version = source.read(PREFIX + 'engine/game_version.txt').decode().strip()
    if version != 'v0.114.0':
        raise ValueError(f'Unexpected game version: {version}')
    manifest_path = ROOT / 'policy_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['upstream'].update(commit=source.commit, game_version=version, source_root=PREFIX.rstrip('/'))
    planned = {}
    for rel in FILES:
        text = source.read(PREFIX + 'python/' + rel).decode('utf-8-sig').replace('\r\n', '\n')
        planned[ROOT / 'policy_sidecar/vendor' / rel] = patch_paths(rel, text).encode()
    planned[ROOT / 'policy_sidecar/vendor/LICENSE'] = source.read('LICENSE')
    planned[ROOT / 'policy_sidecar/vendor/README.md'] = f'''# AlphaDefect inference subset

Copied from https://github.com/hmty-CHEN/AlphaDefect-Public at commit `{source.commit}`,
`{PREFIX}python/` (MIT; see LICENSE). This adapts existing checkpoints to {version};
it does not introduce newly trained weights.

Local changes: `REPO` in gamever.py/catalog.py reads `STS2_POLICY_ROOT`,
`game_version()` honors `STS2_POLICY_GAME_VERSION`, and package initializers omit
headless/training imports (not distributed here). No inference math is changed.

Reproduce with `python game_mod/scripts/vendor-alphadefect-policy.py --upstream <checkout> --apply`
(or `--archive <official-commit.zip>`), paired with `vendor-alphadefect.py` using the same source.
Do not edit these generated files by hand.
'''.encode()
    catalogs = {f'engine/catalog/{name}': PREFIX + f'engine/catalog/{name}'
                for name in ('vocab.txt', 'aliases.txt', 'no_downside_relics.txt', f'{version}/catalog.json')}
    # Training directories are not changed to the target game's version: old columns need their old names.
    for training in sorted({s['content_catalog'] for s in manifest['models'].values()}):
        dest = f'engine/catalog/{training}/catalog.json'
        catalogs.setdefault(dest, dest)
    entries = [s for s in manifest['runtime'] if s['kind'] != 'catalog']
    for dest, upstream_path in catalogs.items():
        blob = source.read(upstream_path)
        entries.append(dict(name=dest.replace('/', '__'), kind='catalog', dest=dest,
                            size=len(blob), sha256=hashlib.sha256(blob).hexdigest(), urls=[
                                f'https://cdn.jsdelivr.net/gh/hmty-CHEN/AlphaDefect-Public@{source.commit}/{upstream_path}',
                                f'https://raw.githubusercontent.com/hmty-CHEN/AlphaDefect-Public/{source.commit}/{upstream_path}']))
    manifest['runtime'] = entries
    planned[manifest_path] = (json.dumps(manifest, ensure_ascii=False, indent=1) + '\n').encode()
    for path, blob in planned.items():
        if not path.exists() or path.read_bytes() != blob:
            print(('write ' if a.apply else 'would write ') + str(path.relative_to(ROOT)))
            if a.apply:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)
    print(f'{source.commit}: game {version}, {len(catalogs)} catalogs; checkpoints unchanged')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
