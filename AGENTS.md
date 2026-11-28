# sts2_autoplay / nekospire — working notes

Two things live in this repo: the N.E.K.O plugin (Python, repo root) and the game mod `nekospire`
(C#, `game_mod/`). The mod serves `http://127.0.0.1:18080`; the plugin drives the game through it.

## Plugin tests (Python)

The tests import `plugin.plugins.sts2_autoplay.*`, which only resolves inside the N.E.K.O host tree, so
plain `pytest tests` from the repo root fails at import. Running them in the deployed copy
(`D:\NekoClaw\N.E.K.O\plugin\plugins\sts2_autoplay`) tests the last deployment, not the working tree.
To test the working tree, use the shim plugin and the host's venv:

```bash
cd tests
D:/NekoClaw/N.E.K.O/.venv/Scripts/python.exe -m pytest -p neko_repo_shim -p no:cacheprovider \
    --rootdir="D:/NekoClaw/n.e.k.o_plugin_sts2_autoplay/tests" -q            # whole suite; runtime depends on the host
```

From the repo root, `./tools/test-working-tree.ps1` wraps the same command. Use `-NekoRoot` for a
non-default host checkout, or `-TestArgs @('-q', 'test_memory_schema.py')` for a targeted run.

- `--rootdir` must be the absolute path of `tests/` (the repo root has an `__init__.py`).
- i18n default checks scan root plugin modules only (not backups/vendor/tooling).
- Pure HTTP/config regressions (no installed game required): `dotnet run --project game_mod/SafetyTests -c Release`.
- `tests/test_t45_solver_gate.py` parses C# sources (`game_mod/nekospire/Solver/CombatSolverFacade.cs`,
  `Solver/Search/CombatPlan.cs`): `loop_runner.SOLVER_SEARCH_STATUSES` must equal the mod's `MapStatus` range.

## Game mod (C#)

```bash
python game_mod/scripts/build-errors.py --all        # non-incremental Release build, errors grouped
dotnet build game_mod/nekospire/nekospire.csproj -c Release
dotnet run --project game_mod/SimTests -c Release     # pure Sim fallback tests, prints ALL PASS
```

- Builds against the installed game (`D:/Steam/.../Slay the Spire 2`) and the RitsuLib workshop item.
  A game update can break `Game/` and `Server/` as well as `Solver/`.
- No Krafs.Publicizer: private game members are read through `Solver/Engine/Common/GameRef.cs`.
- `ilspycmd -t <FullTypeName> <path to sts2.dll>` is installed and is the quickest way to see what a game
  API looks like now.

## Embedded CombatSolver engine

Vendored from `D:\NekoClaw\CombatSolver` (upstream). Read `game_mod/nekospire/Solver/VENDORING.md` first.
Do not modify the upstream checkout: nekospire-only engine changes go into
`game_mod/scripts/vendor_local_patches.py` (replayed on every sync; currently the multiplayer
local-player model). The script vendors upstream's HEAD commit, not its working tree.

```bash
python game_mod/scripts/vendor-combat-solver.py            # dry run
python game_mod/scripts/vendor-combat-solver.py --apply    # take upstream + replay private-access rewrites
```

Do not hand-edit files under `Solver/{Engine,HotKernel,HotSearch,Prediction,Runtime,Search}` — they are
regenerated (a hand edit there is lost on the next sync; put it in `vendor_local_patches.py`). Local code is `Solver/*.cs` plus `Solver/Engine/Common/GameRef.cs`.

Offline smoke test (no game process; drives the built `nekospire.dll` through the `/solver/plan` seam on a
real combat and plays the plan back):

```bash
dotnet build game_mod/nekospire/nekospire.csproj -c Release
dotnet run --project game_mod/SolverSmoke -c Release -- --character IRONCLAD --encounter NibbitsWeak --turns 4
#   options: --room Elite|Boss  --relics A,B  --cards A,B  --potions A,B  --powers P,enemy:P  --seed S  --think 2
#            --teammates SILENT,DEFECT   (idle teammates: exercises the multiplayer player-count rules)
```

It needs the upstream checkout (links its OfflineSearchHarness bootstrap sources). It cannot stand in for an
in-game check: mod loading, the Godot runtime and route quality are only observable in the game.

## AlphaDefect policy seam

One network per character makes every decision of a run (https://github.com/hmty-CHEN/AlphaDefect-Public).
Three hops per step: mod `GET /policy/decision` (candidates + observation frame) -> sidecar `POST /infer`
(`policy_sidecar/alphadefect_server.py`, its own process: the N.E.K.O Python has no torch) ->
mod `POST /action {"action":"policy_act"}`. Plugin side: `policy_client.py`, hooked at the top of
`loop_runner.tick`; whenever it returns None the old solver + heuristic path runs unchanged. A character
without a downloaded model always takes the old path.

Distribution (no paths to configure, nothing downloaded at plugin start):

- `policy_manifest.json` pins every downloadable file (size, SHA-256, mirror URLs): the runtime (embedded
  Python zip + torch/numpy wheels, unpacked without pip), the vocabulary/catalog of one upstream commit, and
  one checkpoint per character. `policy_assets.py` downloads into
  `%LOCALAPPDATA%/N.E.K.O/sts2_autoplay/policy_runtime` only when the download page asks.
- `static/policy.html` is that page: it calls the `sts2_policy_*` entries (`__init__.py`) and reads progress
  from `static/policy_status.json`, which `policy_assets` rewrites while a download runs.
- `policy_sidecar/vendor/` is the inference subset of upstream's Python (MIT; see its README for the two
  path-only changes). It is named `vendor` on purpose: the Market CI lints the whole repo with ruff and
  excludes only directories called `vendor`, and upstream's style does not pass that lint. The sidecar is started by the plugin with the downloaded Python.
- The mod ships no vocabulary (game-derived, not MIT). The plugin sends its copy with `POST /policy/data`
  when the mod's `data_id` differs; the mod keeps it in `user://NekoSpire/policy`.
- NEKO loads the plugin from `%LOCALAPPDATA%/N.E.K.O/.neko-plugin-installations/plugins/sts2_autoplay`
  (installed builds), not from the source tree; `deploy-all.ps1` writes both. Reload with
  `POST http://127.0.0.1:48916/plugin/sts2_autoplay/reload`.

Mod side:

- `game_mod/nekospire/Policy/Vendor` is upstream's observation encoder, regenerated by
  `python game_mod/scripts/vendor-alphadefect.py --apply` and
  `python game_mod/scripts/vendor-alphadefect-policy.py --apply` (do not hand-edit; nekospire's patches are in that
  script; needs an upstream checkout, `D:/NekoClaw/_refs/AlphaDefect-Public`). It also fills the git-ignored
  `Policy/Data`, used only by the offline checks below.
- Both vendoring scripts target upstream `game-v0.114.0/` at the same pinned commit (or accept
  `--archive <official-commit.zip>`). This is compatibility for existing weights, not newly trained models.
  Keep old training catalogs and restart the sidecar after deployment; mixed encoder/catalog versions fail closed.
- `Policy/PolicyDecisionBuilder.cs` mirrors upstream's `RunSession` decision builders (logic only).
  `Policy/PolicyService.cs` reads the live Godot screens and maps candidates to nekospire actions.
- Frame fidelity check: upstream's headless engine plus the built `nekospire.dll` encoding every decision
  of network-driven runs, compared byte for byte. Needs the game files copied as upstream's README
  describes (`~/.local/share/sls2/game/v0.114.0`).

```bash
dotnet build game_mod/nekospire/nekospire.csproj -c Release
dotnet build game_mod/PolicyFidelity/PolicyFidelity.csproj -c Release
D:/NekoClaw/_refs/AlphaDefect-Public/.venv/Scripts/python.exe -I -X utf8 game_mod/scripts/policy_fidelity_run.py \
    --ckpt D:/NekoClaw/_refs/AlphaDefect-Public/weights/files_v6/ironclad_ema_v24782.pt --runs 3
```

- Combat end to end without the game (sidecar must be running, e.g.
  `<policy_runtime>/python/python.exe -I -X utf8 policy_sidecar/alphadefect_server.py --root <policy_runtime> --manifest policy_manifest.json`):
  `SolverSmoke ... --policy http://127.0.0.1:18090` (add `--teammates SILENT` for the multiplayer frame).
- Neither check covers `PolicyService.cs`'s screen reading (rewards, card rewards, card selections, shop,
  chest): no Godot screens exist offline. That part is only observable in the game; `[sts2_policy]` log
  lines say what was chosen and why a step fell back.

## End-of-run review (player's choices vs the recommendation)

Replaces the old LLM reflection and the reflection pause. Three parts:

- Mod `GET /choices?since=N` (`game_mod/nekospire/Game/PlayerChoiceLog.cs`): what the local player chose, read
  from the game's own records (`CombatManager.History`, `PlayerEndedTurn`, the local player's
  `PlayerMapPointHistoryEntry`). No Harmony patch, no screen diffing.
- `choice_review.py`: matches a choice to a network candidate (`match_candidate`) or to the planner's step
  (`planner_recommendation` / `compare_planner`); `ChoiceReview` accumulates one run.
- `service.review_tick` (called from `loop_runner.tick`): claims new choices, then records the current
  recommendation — only while the plugin is not the one acting (`_plugin_is_driving`). At run end
  `_maybe_record_run_memory` stores the summary on the run record (`review`) and `_explain_review` asks the
  LLM about the most divergent steps. It never becomes a lesson and never pauses autoplay.

`policy_fidelity_run.py` (above) also checks this path: every step of the network-driven runs is fed through
`ChoiceReview` with the candidate actually taken as the recommendation; the "wrong" column must be 0. It
needs nekospire's own candidate labels, so the harness takes them from `PolicyFidelityProbe.LastChoices`.
What it cannot cover: a human at the controls in the real game (UI-driven actions, timing between ticks).

## Deploying

`deploy-all.ps1` copies the plugin into N.E.K.O and builds/deploys the mod into the game. The mod is
written to both local `mods/nekospire` and the Workshop directory: the local same/newer version survives
Workshop auto-updates that otherwise replace it before the catgirl child starts. It changes the
user's live install — only run it when asked.


## Handwritten module ownership (A1773 split)

- `service.py` composes `Service*Mixin` classes: configuration in `service_settings.py`, history/review in
  `service_history.py`, host delivery in `service_host.py`, overlay commentary in `service_danmaku.py`,
  status and the static control-page snapshot in `service_status.py`. Patch time/I/O in the owning module in tests.
- `heuristic_planner.py` remains the public `STS2HeuristicPlanner` facade; domain mixins are in
  `planner_{combat,choices,map,shop,lessons}.py`, with shared helpers in `planner_common.py`.
- `GameStateService.cs` and `GameActionService.cs` are partial-class facades. Implementations live in
  their `GameStateService.*.cs` / `GameActionService.*.cs` siblings; `*.Payloads.cs` holds DTOs.
  Source-inspection tests must read all relevant parts, not assume every method is in the facade.
- `static/control_status.json` is generated on runtime status updates; it is not shipped.
  Periodic browser status reads use this snapshot, not new host `/runs` tasks.
- Game artwork is optional under ignored `static/local-art/`, excluded from plugin packaging.
  `tools/ui_smoke.py` deliberately serves it as missing to validate clean-install fallbacks.
- See `docs/module-layout.md` and `docs/report-a1773-review.md` for boundaries and validation.
