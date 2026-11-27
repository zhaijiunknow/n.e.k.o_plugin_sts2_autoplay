"""Pinned 0.114 rules, reproducible vendoring, and mixed-version upgrade regressions."""
import hashlib
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'game_mod/scripts'


def _module(name, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_vendoring_metadata_and_catalogs_agree():
    manifest = json.loads((ROOT / 'policy_manifest.json').read_text(encoding='utf8'))
    vendor = json.loads((ROOT / 'game_mod/nekospire/Policy/VENDORING-MANIFEST.json').read_text(encoding='utf8'))
    assert manifest['upstream']['game_version'] == vendor['upstream']['game_version'] == 'v0.114.0'
    assert manifest['upstream']['commit'] == vendor['upstream']['commit']
    assert vendor['upstream']['root'] == 'game-v0.114.0/engine/Sts2Headless'
    catalogs = {s['dest']: s for s in manifest['runtime'] if s['kind'] == 'catalog'}
    for name, digest in vendor['data'].items():
        assert catalogs[f'engine/catalog/{name}']['sha256'] == digest
    for rel, digest in vendor['files'].items():
        text = (ROOT / 'game_mod/nekospire/Policy/Vendor' / rel).read_text(encoding='utf8')
        assert hashlib.sha256(text.encode()).hexdigest() == digest, rel


def test_114_raw_damage_hook_is_installed_and_scoped():
    hooks = (ROOT / 'game_mod/nekospire/Policy/PolicyHooks.cs').read_text(encoding='utf8')
    assert 'Observation.RawPreview.Install(h);' in hooks
    raw = (ROOT / 'game_mod/nekospire/Policy/Vendor/Engine/Obs/RawPreview.cs').read_text(encoding='utf8')
    assert '[ThreadStatic]' in raw
    assert 'if (!RawDamagePreview) return true;' in raw


def test_runtime_path_patches_fail_if_upstream_changes(monkeypatch):
    mod = _module('vendor-alphadefect-policy', monkeypatch)
    with pytest.raises(ValueError, match='no longer matches'):
        mod.patch_paths('sts2env/gamever.py', 'REPO = new_layout\n')
    assert mod.patch_paths('sts2rl/model.py', 'unchanged\n') == 'unchanged\n'
    assert 'from .env' not in mod.patch_paths('sts2env/__init__.py', 'from .env import Sts2Env\n')
    assert 'from .env' not in (ROOT / 'policy_sidecar/vendor/sts2env/__init__.py').read_text(encoding='utf8')


def test_archive_requires_immutable_commit_and_reads_without_extracting(tmp_path, monkeypatch):
    mod = _module('alphadefect_source', monkeypatch)
    archive = tmp_path / 'source.zip'
    with ZipFile(archive, 'w') as z:
        z.writestr('AlphaDefect-Public-main/test', 'no')
    with pytest.raises(ValueError, match='immutable'):
        mod.UpstreamSource(tmp_path, archive)
    commit = 'a' * 40
    with ZipFile(archive, 'w') as z:
        z.writestr(f'AlphaDefect-Public-{commit}/engine/version.txt', 'v0.114.0')
    source = mod.UpstreamSource(tmp_path, archive)
    assert source.commit == commit
    assert source.read('engine/version.txt') == b'v0.114.0'
    assert source.files('engine/') == ['engine/version.txt']
    source.archive.close()
