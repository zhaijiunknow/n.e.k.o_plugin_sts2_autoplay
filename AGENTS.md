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
    --rootdir="D:/NekoClaw/n.e.k.o_plugin_sts2_autoplay/tests" -q            # whole suite: ~8 min
```

- `--rootdir` must be the absolute path of `tests/` (the repo root has an `__init__.py`).
- Known unrelated failure when run from the repo: `test_i18n_first_person.py::test_ab_layer_code_defaults_are_also_first_person`
  scans every `*.py` under the plugin root and trips over old backups in the untracked `.dsh-artifacts/`.
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

## Deploying

`deploy-all.ps1` copies the plugin into N.E.K.O and builds/deploys the mod into the game. It changes the
user's live install — only run it when asked.
