#!/usr/bin/env python3
"""Vendor the AlphaDefect observation encoder into nekospire (Policy/Vendor).

AlphaDefect (https://github.com/hmty-CHEN/AlphaDefect-Public, MIT) trains one network per character that
makes every decision of a run. The network reads binary observation frames; the encoder that produces them
is shared source between upstream's headless engine and its (unreleased) live bridge mod. We take that
encoder as-is so the frames nekospire emits are the frames the network was trained on.

    python game_mod/scripts/vendor-alphadefect.py            # dry run: report what would change
    python game_mod/scripts/vendor-alphadefect.py --apply    # replace Policy/Vendor + Policy/Data

What it does (--apply):
  1. deletes stale *.cs under Policy/Vendor (stale files would keep compiling otherwise),
  2. copies the encoder closure and upstream's LICENSE from upstream's HEAD commit or a fixed-commit GitHub ZIP (never its working
     tree; LF, no BOM),
  3. applies nekospire's own patches below — each carries the number of hits it must produce, so an
     upstream change that moves the patched code fails the script instead of silently dropping a patch,
  4. copies the runtime data the encoder reads (vocabulary, aliases, no-downside relic list) into
     Policy/Data. Those tables are derived from game data and are NOT covered by upstream's MIT license
     (see its NOTICE.md); they are git-ignored here and copied next to the mod DLL at deploy time.

Upstream is never modified. Everything outside the closure that the encoder touches (RunSession,
RewardsFlow, FakeScreens, ...) belongs to upstream's headless host; nekospire supplies live-game
stand-ins under Policy/Shim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from alphadefect_source import UpstreamSource

GAME_MOD = Path(__file__).resolve().parents[1]
POLICY = GAME_MOD / "nekospire" / "Policy"
VENDOR = POLICY / "Vendor"
DATA = POLICY / "Data"
MANIFEST_PATH = POLICY / "VENDORING-MANIFEST.json"

SOURCE_PREFIX = "game-v0.114.0/"
UPSTREAM_ROOT = SOURCE_PREFIX + "engine/Sts2Headless"
# Whole subtrees and single files of the encoder closure, relative to UPSTREAM_ROOT.
SUBTREES = ["Engine/Obs", "Engine/Compat"]
FILES = [
    "Engine/Observation.cs",
    "Engine/ObservationOracle.cs",
    "Engine/Decision.cs",
    "Engine/Vocab.cs",
]
DATA_FILES = {
    SOURCE_PREFIX + "engine/catalog/vocab.txt": "vocab.txt",
    SOURCE_PREFIX + "engine/catalog/aliases.txt": "aliases.txt",
    SOURCE_PREFIX + "engine/catalog/no_downside_relics.txt": "no_downside_relics.txt",
}

# --- nekospire patches ----------------------------------------------------------------------------
# (file relative to UPSTREAM_ROOT, old, new, expected hits, why)
LOCAL_PATCHES: list[tuple[str, str, str, int, str]] = []


def _local_player(rel: str, hits: int) -> None:
    LOCAL_PATCHES.append((
        rel, "RunSession.State.Players[0]", "RunSession.Me", hits,
        "multiplayer: the observer is the local player, not run slot 0",
    ))


_local_player("Engine/Obs/Creatures.cs", 2)
_local_player("Engine/Obs/Creatures29.cs", 3)
_local_player("Engine/Obs/Events.cs", 2)
LOCAL_PATCHES += [
    ("Engine/Observation.cs", "var me = s.Players[0];", "var me = RunSession.Me;", 1,
     "multiplayer: the observer is the local player, not run slot 0"),
]


# v0.114.0 upstream now handles removed arrow/melody/free-card APIs itself.
# Keep the one remaining local API bridge; every patch still has an exact hit count.
LOCAL_PATCHES += [
    ("Engine/Obs/Cards.cs", "copy.UpgradeInternal();",
     "NekoComm.Game.GameApiCompat.UpgradeInternal(copy);", 1, "binary-compatible upgrade preview across game versions"),
]


def upstream_files(source: UpstreamSource) -> dict[str, str]:
    out = {}
    for path in source.files(UPSTREAM_ROOT + "/"):
        rel = path[len(UPSTREAM_ROOT) + 1:]
        if not rel.endswith(".cs") or not (rel in FILES or any(rel.startswith(d + "/") for d in SUBTREES)):
            continue
        out[rel] = source.read(path).decode("utf-8-sig").replace("\r\n", "\n")
    return out


def apply_patches(files: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for rel, old, new, expected, why in LOCAL_PATCHES:
        if rel not in files:
            problems.append(f"{rel}: patched file is not in the closure ({why})")
            continue
        hits = files[rel].count(old)
        if hits != expected:
            problems.append(f"{rel}: expected {expected} x `{old}`, found {hits} ({why})")
            continue
        files[rel] = files[rel].replace(old, new)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--upstream", default=r"D:\NekoClaw\_refs\AlphaDefect-Public")
    parser.add_argument("--archive", type=Path, help="Official GitHub ZIP for an immutable commit (alternative to git)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    upstream = Path(args.upstream)
    if not args.archive and not (upstream / ".git").exists():
        print(f"upstream checkout not found: {upstream}", file=sys.stderr)
        return 2

    source = UpstreamSource(upstream, args.archive)
    head = source.commit
    game_version = source.read(f"{SOURCE_PREFIX}engine/game_version.txt").decode().strip()
    files = upstream_files(source)
    problems = apply_patches(files)
    if problems:
        print("local patches no longer match upstream:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    data = {dst: source.read(src) for src, dst in DATA_FILES.items()}

    planned = {VENDOR / rel: text.encode("utf-8") for rel, text in files.items()}
    planned[VENDOR / "LICENSE-AlphaDefect.txt"] = source.read("LICENSE")
    planned.update({DATA / name: blob for name, blob in data.items()})
    stale = [p for p in VENDOR.rglob("*.cs") if p not in planned] if VENDOR.exists() else []
    changed = [p for p, blob in planned.items() if not p.exists() or p.read_bytes() != blob]
    print(f"upstream {head[:8]} (game {game_version}): closure {len(files)} files, "
          f"{len(LOCAL_PATCHES)} local patches, {len(data)} data files")
    print(f"to write: {len(changed)}, stale to delete: {len(stale)}")
    if not args.apply:
        for p in changed[:20]:
            print("  write  " + str(p.relative_to(GAME_MOD)))
        for p in stale[:20]:
            print("  delete " + str(p.relative_to(GAME_MOD)))
        return 0

    for p in stale:
        p.unlink()
    for p, blob in planned.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    manifest = {
        "schema": "nekospire-alphadefect-vendoring/1",
        "upstream": {
            "repository": "https://github.com/hmty-CHEN/AlphaDefect-Public",
            "commit": head,
            "game_version": game_version,
            "license": "MIT (code); vocabulary and catalogs are game-derived and not covered",
            "root": UPSTREAM_ROOT,
        },
        "local_patches": [
            {"file": rel, "old": old, "new": new, "hits": hits, "why": why}
            for rel, old, new, hits, why in LOCAL_PATCHES
        ],
        "files": {rel: hashlib.sha256(text.encode("utf-8")).hexdigest() for rel, text in sorted(files.items())},
        "data": {name: hashlib.sha256(blob).hexdigest() for name, blob in sorted(data.items())},
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"applied; manifest -> {MANIFEST_PATH.relative_to(GAME_MOD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
