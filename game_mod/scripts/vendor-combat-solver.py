#!/usr/bin/env python3
"""Re-vendor the embedded CombatSolver engine (HotKernel architecture, upstream >= 1.0.0).

Reproducible pipeline: the vendor boundary lives in Solver/VENDORING-RULES.json (hand-written, single
source of truth); the private-member adaptation recipes live here, next to the code that applies them.

    python game_mod/scripts/vendor-combat-solver.py            # dry run: report what would change
    python game_mod/scripts/vendor-combat-solver.py --apply    # replace the vendored closure + adapt
    python game_mod/scripts/vendor-combat-solver.py --manifest # only rewrite VENDORING-MANIFEST.json

What it does (--apply):
  1. deletes every *.cs under the vendored subtrees (stale files would keep compiling otherwise),
  2. copies the upstream closure named by the rules file (LF, no BOM),
  3. restores the local files listed in `always_preserve_local`,
  4. rewrites every direct access to a non-public game member into a GameRef reflection call,
  5. applies nekospire's own behaviour patches (vendor_local_patches.py). Upstream is never modified.

Step 4 is strict on purpose: every recipe carries the number of hits it must produce. An upstream change
that adds or removes a private access makes the count differ and the script fails instead of silently
shipping a build that throws FieldAccessException in the game (Krafs.Publicizer is not honoured by the
mod loader for this plain-SDK mod — see Solver/Engine/Common/GameRef.cs).

After --apply: `python game_mod/scripts/build-errors.py` must report 0 errors; any CS0122 / CS1061 /
CS0117 it lists is a NEW private access that needs a recipe here.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from vendor_local_patches import LOCAL_PATCHES

GAME_MOD = Path(__file__).resolve().parents[1]
SOLVER = GAME_MOD / "nekospire" / "Solver"
RULES_PATH = SOLVER / "VENDORING-RULES.json"
MANIFEST_PATH = SOLVER / "VENDORING-MANIFEST.json"

# --- recipes -------------------------------------------------------------------------------------
# Non-public instance members read as `<receiver>.<member>`; value = C# type handed to GameRef.Get<T>.
# Types come from the real sts2.dll metadata (game v0.113.1).
PRIVATE_MEMBERS: dict[str, str] = {
    # CombatState / CardModel / CardEnergyCost / MonsterModel
    "_nextCreatureId": "uint",
    "_hasSingleTurnRetain": "bool",
    "_base": "int",
    "_keywordsAddedFromOutside": "HashSet<MegaCrit.Sts2.Core.Entities.Cards.CardKeyword>",
    "_keywordsRemovedFromOutside": "HashSet<MegaCrit.Sts2.Core.Entities.Cards.CardKeyword>",
    "_localModifiers": "List<MegaCrit.Sts2.Core.Entities.Cards.LocalCostModifier>",
    "_temporaryStarCosts": "List<MegaCrit.Sts2.Core.Entities.Cards.TemporaryCardCost>",
    "_creature": "MegaCrit.Sts2.Core.Entities.Creatures.Creature",
    # powers
    "_proccdThisTurn": "bool",
    "_shouldApplyIntangible": "bool",
    # cards (decimal-valued scaling counters)
    "ExtraDamageFromClawPlays": "decimal",
    "_extraDamageFromMaulPlays": "decimal",
    "ExtraDamageFromPlays": "decimal",
    "_cardsExhaustedByFlorescence": "decimal",
    # relic counters / latches
    "_triggeredThisTurn": "bool",
    "_usedThisTurn": "bool",
    "_usedThisCombat": "bool",
    "_wasOwnerPartOfLastPlayerTurn": "bool",
    "_hadLeftoverEnergy": "bool",
    "_attacksPlayedThisTurn": "int",
    "_skillsPlayedThisTurn": "int",
    "_powersPlayedThisTurn": "int",
    "_activationCountThisTurn": "int",
    "_strengthApplied": "bool",
    "_cardsPlayedThisTurn": "int",
    "_cardsPlayedLastTurn": "int",
    "_damageReceivedThisTurn": "decimal",
    "WasUsedThisCombat": "bool",
    "_etherealCount": "int",
    "_orbsChanneled": "int",
    "_wasUsedThisTurn": "bool",
    "_cooldown": "int",
    "_triggeredBlockLastTurn": "bool",
    "_affectedCardPlay": "object?",
    "_activatedThisCombat": "bool",
    "_blockGainedThisCombat": "bool",
}

# Fields of the non-public nested `XPower.Data` classes returned by PowerModel.GetInternalData<T>()
# (protected generic; the nested type cannot even be named from here).
INTERNAL_DATA_FIELDS: dict[str, str] = {
    "selectedCard": "MegaCrit.Sts2.Core.Models.CardModel?",
    "commandToModify": "MegaCrit.Sts2.Core.Commands.Builders.AttackCommand?",
    "numberOfCardsPlayedSinceApplied": "int",
    "triggeredThisTurn": "bool",
    "casters": "HashSet<MegaCrit.Sts2.Core.Entities.Creatures.Creature>",
    "downgradedCardsToOldUpgradeLevels": "Dictionary<MegaCrit.Sts2.Core.Models.CardModel, int>",
    "boundCardPlayed": "bool",
    "attacksPlayedThisTurn": "int",
    "cardToClone": "MegaCrit.Sts2.Core.Models.CardModel?",
    "zeroCostAttacksPlayed": "int",
}

RNG_CAPTURE_OLD = """    public static HotRngState CaptureRng(Rng rng) => new()
    {
        S0 = rng._random._s0,
        S1 = rng._random._s1,
        S2 = rng._random._s2,
        S3 = rng._random._s3,
        Counter = rng._counter,
        Pad = 0,
    };
"""
# Rng.ToSerializable() is public and carries exactly the counter + the four xoshiro state words, so
# this needs no private access at all.
RNG_CAPTURE_NEW = """    public static HotRngState CaptureRng(Rng rng)
    {
        MegaCrit.Sts2.Core.Saves.SerializableRng serialized = rng.ToSerializable();
        return new()
        {
            S0 = serialized.state0,
            S1 = serialized.state1,
            S2 = serialized.state2,
            S3 = serialized.state3,
            Counter = serialized.counter,
            Pad = 0,
        };
    }
"""

# file (relative to Solver/) -> expected hit counts per recipe kind
EXPECTED: dict[str, dict[str, int]] = {
    "HotKernel/Import/HotImportSupport.cs": {"member": 7, "rng": 1},
    "HotKernel/Import/HotRootBuilder.cs": {"member": 48, "data_inline": 6, "data_local_decl": 2, "data_local_use": 6},
    "HotKernel/Import/VanillaRootGate.cs": {"data_inline": 1},
    "Search/PotionRewardOutlook.cs": {"exact": 1},
}

EXACT: dict[str, list[tuple[str, str]]] = {
    "Search/PotionRewardOutlook.cs": [
        ("case SilkenTress { IsUsed: false }:",
         'case SilkenTress tress when !GameRef.Get<bool>(tress, "IsUsed"):'),
    ],
}

RECEIVER = r"(?<![\w.])(?P<recv>[A-Za-z_]\w*(?:\.EnergyCost)?)"
MEMBER_RE = re.compile(RECEIVER + r"\.(?P<member>" + "|".join(map(re.escape, PRIVATE_MEMBERS)) + r")\b(?!\s*[(=][^=])")
DATA_INLINE_RE = re.compile(
    r"(?P<recv>[A-Za-z_]\w*)\.GetInternalData<\w+\.Data>\(\)\.(?P<field>\w+)\b")
DATA_LOCAL_DECL_RE = re.compile(
    r"\b\w+\.Data (?P<local>\w+) = (?P<recv>[A-Za-z_]\w*)\.GetInternalData<\w+\.Data>\(\);")


def adapt(rel: str, text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}

    if RNG_CAPTURE_OLD in text:
        counts["rng"] = text.count(RNG_CAPTURE_OLD)
        text = text.replace(RNG_CAPTURE_OLD, RNG_CAPTURE_NEW)

    for old, new in EXACT.get(rel, []):
        counts["exact"] = counts.get("exact", 0) + text.count(old)
        text = text.replace(old, new)

    # `x.GetInternalData<P.Data>().field`
    def data_inline(match: re.Match[str]) -> str:
        field = match["field"]
        if field not in INTERNAL_DATA_FIELDS:
            raise SystemExit(f"{rel}: unknown internal-data field '{field}' — add it to INTERNAL_DATA_FIELDS")
        return (f'GameRef.Get<{INTERNAL_DATA_FIELDS[field]}>('
                f'GameRef.InvokeGeneric({match["recv"]}, "GetInternalData", "Data")!, "{field}")')

    text, counts["data_inline"] = DATA_INLINE_RE.subn(data_inline, text)

    # `P.Data data = x.GetInternalData<P.Data>();` then `data.field` inside the same case block
    locals_declared: list[str] = []

    def data_local_decl(match: re.Match[str]) -> str:
        locals_declared.append(match["local"])
        return f'object {match["local"]} = GameRef.InvokeGeneric({match["recv"]}, "GetInternalData", "Data")!;'

    text, counts["data_local_decl"] = DATA_LOCAL_DECL_RE.subn(data_local_decl, text)
    counts["data_local_use"] = 0
    for local in sorted(set(locals_declared)):
        use_re = re.compile(
            r"(?<![\w.])" + re.escape(local) + r"\.(?P<field>" + "|".join(map(re.escape, INTERNAL_DATA_FIELDS)) + r")\b")
        text, hits = use_re.subn(
            lambda m: f'GameRef.Get<{INTERNAL_DATA_FIELDS[m["field"]]}>({local}, "{m["field"]}")', text)
        counts["data_local_use"] += hits

    text, counts["member"] = MEMBER_RE.subn(
        lambda m: f'GameRef.Get<{PRIVATE_MEMBERS[m["member"]]}>({m["recv"]}, "{m["member"]}")', text)

    return text, {kind: count for kind, count in counts.items() if count}


def load_upstream_head(upstream: Path) -> dict[str, str]:
    """Every src/**/*.cs of upstream's HEAD commit (LF, no BOM), keyed by path relative to src/.

    Read from the commit, not from the working tree: the manifest records a commit hash, so the content
    must be that commit's — uncommitted edits in the upstream checkout must never leak into the vendor.
    """
    data = subprocess.run(["git", "-C", str(upstream), "archive", "--format=tar", "HEAD", "src"],
                          capture_output=True, check=True).stdout
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            if member.isfile() and member.name.endswith(".cs"):
                raw = archive.extractfile(member).read()
                files[member.name[len("src/"):]] = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return files


def read_lf(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def write_manifest(rules: dict, version: str, head: str, planned: dict[str, str], preserve: list[str]) -> None:
    """Generated census of the vendored closure (do not hand-edit): which upstream commit each file
    came from, its content hash as vendored, and which files carry private-access rewrites."""
    by_subtree: dict[str, int] = {}
    for rel in planned:
        by_subtree[rel.split("/", 1)[0]] = by_subtree.get(rel.split("/", 1)[0], 0) + 1
    manifest = {
        "schema": "nekospire-combat-solver-vendoring/2",
        "generated_by": "game_mod/scripts/vendor-combat-solver.py",
        "upstream": {
            "repository": rules["upstream"]["repository"],
            "version": version,
            "commit": head,
            "license": rules["upstream"]["license"],
            "holders": rules["upstream"]["license_holders"],
        },
        "totals": {
            "vendored_files": len(planned),
            "by_subtree": dict(sorted(by_subtree.items())),
            "adapted_files": len(EXPECTED),
            "private_access_rewrites": sum(sum(counts.values()) for counts in EXPECTED.values()),
        },
        "preserved_local_files": preserve,
        "local_files": [entry["path"] for entry in rules.get("local_files", [])],
        "adapted": {rel: counts for rel, counts in sorted(EXPECTED.items())},
        "local_patches": {rel: len(pairs) for rel, pairs in sorted(LOCAL_PATCHES.items())},
        "files": [
            {
                "path": rel,
                "lines": len(text.splitlines()),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "verbatim": rel not in EXPECTED and rel not in LOCAL_PATCHES,
            }
            for rel, text in sorted(planned.items())
        ],
    }
    MANIFEST_PATH.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--upstream", default=r"D:\NekoClaw\CombatSolver")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", action="store_true",
                        help="rewrite VENDORING-MANIFEST.json (requires the tree to already match upstream)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    upstream = Path(args.upstream)
    head = subprocess.run(["git", "-C", str(upstream), "rev-parse", "--short=8", "HEAD"],
                          capture_output=True, check=True, text=True).stdout.strip()
    version = json.loads(subprocess.run(
        ["git", "-C", str(upstream), "show", "HEAD:CombatSolver.json"],
        capture_output=True, check=True).stdout.decode("utf-8-sig"))["version"]
    upstream_files = load_upstream_head(upstream)
    dirty = subprocess.run(["git", "-C", str(upstream), "status", "--porcelain"],
                           capture_output=True, check=True, text=True).stdout.splitlines()
    expected = rules["upstream"]
    print(f"upstream {version} @ {head}   (rules expect {expected['expected_version']} @ {expected['expected_commit']})")
    if version != expected["expected_version"] or not head.startswith(expected["expected_commit"][:7]):
        print("  !! upstream differs from the rules file — re-check the boundary and the recipes before trusting this run")
    if dirty:
        print(f"  note: the upstream checkout has {len(dirty)} uncommitted change(s); they are ignored (vendoring HEAD)")

    excluded = set(rules.get("exclude_files", {}))
    closure: list[str] = []
    for sub in rules["vendor_subtrees"]:
        closure += [rel for rel in upstream_files if rel.startswith(sub + "/") and rel not in excluded]
    closure += rules["vendor_files"]
    closure = sorted(set(closure))
    preserve = [entry["path"].removeprefix("Solver/") for entry in rules["always_preserve_local"]]
    owned_dirs = sorted({rel.split("/", 1)[0] for rel in closure})

    planned: dict[str, str] = {}
    failures: list[str] = []
    for rel in closure:
        text = upstream_files[rel]
        # Only files with registered recipes are rewritten; everything else is taken verbatim (the
        # kernel's own structs have `_base`-style fields too, so a blanket rewrite would be wrong).
        if rel in EXPECTED:
            text, counts = adapt(rel, text)
            if counts != EXPECTED[rel]:
                failures.append(f"{rel}: adaptation hits {counts} != expected {EXPECTED[rel]}")
        for old, new in LOCAL_PATCHES.get(rel, []):
            hits = text.count(old)
            if hits != 1:
                failures.append(f"{rel}: local patch anchor matched {hits} times (expected 1): {old.strip().splitlines()[0][:70]!r}")
                continue
            text = text.replace(old, new)
        planned[rel] = text
    for rel in list(EXPECTED) + list(LOCAL_PATCHES):
        if rel not in planned:
            failures.append(f"{rel}: has recipes but is no longer part of the vendored closure")

    stale = sorted(
        str(path.relative_to(SOLVER)).replace("\\", "/")
        for sub in owned_dirs if (SOLVER / sub).is_dir()
        for path in (SOLVER / sub).rglob("*.cs")
        if str(path.relative_to(SOLVER)).replace("\\", "/") not in planned
        and str(path.relative_to(SOLVER)).replace("\\", "/") not in preserve)
    changed = [rel for rel, text in planned.items()
               if not (SOLVER / rel).is_file() or read_lf(SOLVER / rel) != text]

    print(f"closure: {len(closure)} files in {owned_dirs}; adapted files: {len(EXPECTED)}; "
          f"locally patched files: {len(LOCAL_PATCHES)}")
    print(f"would write {len(changed)} file(s), remove {len(stale)} stale file(s)")
    for rel in changed[:20]:
        print(f"  ~ {rel}")
    for rel in stale[:20]:
        print(f"  - {rel}")
    if failures:
        print("FAILED — recipes no longer match upstream:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    if args.manifest and not args.apply and (changed or stale):
        print("refusing to write the manifest: the vendored tree does not match upstream (run --apply)")
        return 1
    if args.apply or args.manifest:
        write_manifest(rules, version, head, planned, preserve)
    if not args.apply:
        print("manifest written" if args.manifest else "dry run (pass --apply to write)")
        return 0

    kept = {rel: (SOLVER / rel).read_bytes() for rel in preserve if (SOLVER / rel).is_file()}
    for sub in owned_dirs:
        if (SOLVER / sub).is_dir():
            shutil.rmtree(SOLVER / sub)
    for rel, text in planned.items():
        target = SOLVER / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    for rel, data in kept.items():
        target = SOLVER / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(f"applied. preserved local files: {sorted(kept)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
