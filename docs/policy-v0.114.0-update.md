# AlphaDefect v0.114.0 adaptation (2026-11-27)

## Upstream verification

GitHub API `repos/hmty-CHEN/AlphaDefect-Public/commits` and `releases` were queried on
2026-11-27. Target commit: `f77831b6ea77b8ece32daf1912154ae081da875c` (2026-11-25).
The adaptation was introduced by `d5b3c384b4d36c8239d36b2b99e1e3b44a1c3dcb`.
Read the pinned upstream `game-v0.114.0/README.md`: this is game compatibility code,
**not newly trained v0.114.0 checkpoints**. Latest published weights release remains
`weights-v7` (2026-11-22), which contains starting checkpoints for research comparisons,
not replacements for the distilled Defect/Regent policies currently shipped here.

The six model files, release URLs, sizes, SHA-256 values, and `content_catalog` values
remain unchanged. Do not relabel a training catalog as v0.114.0: old feature columns must
still be interpreted using their actual training catalog.

## Changes

- C# observation closure and Python inference subset come from the upstream
  `game-v0.114.0/` subtree at the same commit.
- Catalog and vocabulary use v0.114.0 (2,559 IDs); old IDs retain their positions,
  OPTIMISM and TWIN_SHOT are appended, and TWIN_SHOT aliases RAPID_SHOT.
- Both v0.111.0 and v0.113.1 training catalogs remain downloadable and hash-pinned.
  The sidecar retains explicit feature-name alignment and zeroes new vocabulary rows.
- Upstream Fool rule fixes, Hard to Kill handling, and the thread-scoped raw-damage
  preview hook are included. The hook is installed by `PolicyHooks`, not just compiled.
- Removed upstream APIs no longer need local arrow/melody/free-card workarounds.
  Counted multiplayer-local-player and UpgradeInternal patches are preserved.
- Sidecar health reports its pinned upstream identity. A plugin rejects a stale
  sidecar or a mod lacking the matching `encoder_game_version`, instead of silently
  mixing old observation semantics with new catalogs.
- Vendoring is reproducible from a clean Git revision or an official GitHub commit ZIP;
  it never copies uncommitted upstream files. Downloaded catalogs are not vendored into
  the repository because they contain game-derived data.

## Reproduce

```powershell
python game_mod/scripts/vendor-alphadefect.py --upstream <updated-checkout> --apply
python game_mod/scripts/vendor-alphadefect-policy.py --upstream <same-checkout> --apply
# Alternatively, pass --archive <AlphaDefect-Public-40-character-commit.zip> to both.
dotnet build game_mod/nekospire/nekospire.csproj -c Release --no-restore
./tools/test-working-tree.ps1 -TestArgs @('-q','test_policy_assets.py','test_policy_client.py','test_policy_sidecar.py','test_policy_114_upgrade.py')
```

`PolicyFidelity` now links the v0.114.0 subtree and expects v0.114.0 game files. Its
Python runner must use the same checkout; do not compare the new encoder with the old
v0.113.1 headless engine.

## Installation implications

This change is source-only until explicitly deployed. Install plugin and mod together,
exit/restart the game, and restart the inference sidecar. On the policy page run the
runtime preparation/repair once to fetch the changed catalogs and vocabulary. Python,
torch and the six model files do not need replacement if already intact. There are
still no downloads at plugin startup. Until data is repaired, the plugin falls back
rather than inferring from incompatible data.

Offline tests do not establish live Godot screen compatibility or a new win rate.

## Verification results

- Retrieved 149 required upstream files (unchanged files reused from the existing checkout),
  verified each Git blob SHA-1 against the official recursive tree for the pinned commit.
  Full Git clone failed with HTTP 502; the redundant large archive download was stopped.
  The verified files were assembled into a commit-rooted ZIP for reproducible vendoring.
- Both vendor scripts are idempotent: a second dry run reports no changes.
- Mod Release build: 0 errors, 9 existing nullable-reference warnings.
- PolicyFidelity Release build: 0 errors, 0 warnings.
- Targeted assets/client/sidecar/upgrade/livestream tests: 161 passed (86 existing unknown-marker warnings).
  After adding the package-initializer regression, focused retest: 60 passed.
- Real downloaded runtime: all six existing model SHA-256 values match their pinned release specs;
  all six load, align content columns by name, and zero the two newly appended vocabulary rows.
- Sidecar + headless game smoke: 64 inferred decisions per character, 384 total;
  finite logits, normalized probabilities, valid selected candidates, zero encoder mismatches.
  Saved frame replay via `tests/policy_runtime_smoke.py --frames`: 48 frames passed.
- One full A10 run per character on seed `24VQ8UQR7J`: 5,114 observation frames total,
  0 differences versus upstream (Ironclad 904, Silent 1,029, Defect 809, Regent 989,
  Necrobinder 837, Fool 546). These are compatibility checks, NOT win-rate estimates.
- **Not fully green:** the separate player-choice review check reports 2 Regent multi-card
  selection mismatches (floors 20 and 39) and 1 Necrobinder mismatch (floor 43).
  All their observation frames match; these choice-log/recommendation matches remain unresolved.
  Accordingly, the Regent and Necrobinder full-run fidelity commands return exit code 1.
  No claim is made that every whole-run assertion passed.
- Changed Python files pass Ruff; `git diff --check` reports no whitespace errors.

Logs are under ignored `tmp/policy114-*`. No active runtime data or installed game files
were modified; all test catalogs and game data were isolated under the repository's `tmp/`.
No deployment, publishing or push was performed. Live Godot screens still need in-game testing.
