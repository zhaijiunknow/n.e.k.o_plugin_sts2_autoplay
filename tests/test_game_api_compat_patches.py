"""Compatibility recipes must be reproducible, hash-checked and safe to replay offline."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "game_mod" / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return load("vendor-combat-solver")


def test_solver_manifest_and_replay_are_current(vendor):
    rules = json.loads(vendor.RULES_PATH.read_text(encoding="utf-8"))
    assert vendor.refresh_local_patches(rules, apply=False) == 0
    manifest = json.loads(vendor.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["local_patches"] == {rel: len(pairs) for rel, pairs in vendor.LOCAL_PATCHES.items()}
    for rel, pairs in vendor.LOCAL_PATCHES.items():
        patched = vendor.read_lf(vendor.SOLVER / rel)
        # Recover the input to these recipes, then run the exact upstream replay (one hit each).
        original = patched
        for old, new in reversed(pairs):
            assert original.count(new) == 1
            original = original.replace(new, old)
        for old, new in pairs:
            assert original.count(old) == 1
            original = original.replace(old, new)
        assert original == patched


def test_refresh_refuses_modified_tree_before_writing(vendor, monkeypatch, tmp_path):
    path = tmp_path / "sample.cs"
    path.write_text("unexpected edit", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": [{"path": "sample.cs", "sha256": hashlib.sha256(b"original").hexdigest()}]}))
    monkeypatch.setattr(vendor, "SOLVER", tmp_path)
    monkeypatch.setattr(vendor, "MANIFEST_PATH", manifest)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        vendor.refresh_local_patches({}, apply=True)
    assert path.read_text() == "unexpected edit"


@pytest.mark.parametrize("text", ["absent", "old old", "new new", "new old"])
def test_ambiguous_patch_fails(vendor, text):
    with pytest.raises(ValueError):
        vendor.apply_local_patch_updates(text, [("old", "new")])


def test_patch_is_idempotent(vendor):
    assert vendor.apply_local_patch_updates("old", [("old", "new")]) == "new"
    assert vendor.apply_local_patch_updates("new", [("old", "new")]) == "new"


def test_policy_patches_replay_and_manifest_matches():
    vendor = load("vendor-alphadefect")
    manifest = json.loads(vendor.MANIFEST_PATH.read_text(encoding="utf-8"))
    files = {rel: (vendor.VENDOR / rel).read_text(encoding="utf-8") for rel in manifest["files"]}
    for rel, digest in manifest["files"].items():
        assert hashlib.sha256(files[rel].encode()).hexdigest() == digest
    original = files.copy()
    for rel, old, new, hits, _ in reversed(vendor.LOCAL_PATCHES):
        assert original[rel].count(new) == hits
        original[rel] = original[rel].replace(new, old)
    assert vendor.apply_patches(original) == []
    assert original == files


def test_both_arrow_destroy_writers_are_capability_gated():
    text = (ROOT / "game_mod/nekospire/Solver/HotKernel/Sim/Content/HotFoolTarot.cs").read_text(encoding="utf-8")
    lines = text.splitlines()
    writes = [i for i, line in enumerate(lines) if "|= ArrowDestroyBit" in line]
    assert len(writes) == 2
    assert all("GameApiCompat.SupportsArrowDestruction" in lines[i - 1] for i in writes)
