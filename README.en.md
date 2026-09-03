## Third-Party Credits

This Mod vendors and adapts the combat-solver core of the following third-party projects:

- **[Combat Solver](https://github.com/Torch1230/CombatSolver)** (author: **Torch / Torch1230**) — combat route solver. Its **0.28 search/simulation core** is embedded and adapted here to drive `GET /solver/plan` combat recommendations. Used with the author's permission.
  - Card-effect inference uses **RitsuLib**'s `HarmonyIl` / `RitsuLibFramework.GetMaxHandSize` / `IComputedDynamicVar` (higher fidelity: no more `unsupported` card models).
  - The engine's direct access to private game members is routed through **`GameRef`** reflection (the port does not rely on compile-time/run-time publicization).
  - **`Krafs.Publicizer` is not used** (pure `GameRef` reflection is sufficient to build & run).
- **[Random Foreseer](https://github.com/hotwords123/StS2.RandomForeseer)** (author: **hotwords123**) — [Workshop page](https://steamcommunity.com/sharedfiles/filedetails/?id=3747531952). Combat Solver's combat-simulation engine core (combat state, piles, RNG, fork, history, mirror) derives partly from this, reworked under hotwords123's written permission. This Mod vendors the engine source only and does **not** load/distribute the Random Foreseer assembly as a runtime dependency.

> Third-party source follows the **original authors' licensing boundaries**: Combat Solver does not ship a unified repository license; Random Foreseer-derived code is governed by Combat Solver's `THIRD_PARTY_NOTICES.md`. The above is not automatically covered by this repository's AGPL-3.0-only.

## Runtime dependency: RitsuLib

This Mod's combat solver depends on the third-party shared framework **[STS2-RitsuLib](https://steamcommunity.com/sharedfiles/filedetails/?id=3747602295)** (card-effect IL inference, etc.). Make sure:
- The **STS2-RitsuLib** Workshop mod is subscribed (auto-downloaded to `steamapps/workshop/content/2868840/3747602295/`);
- A resolvable `STS2-RitsuLib.dll` is present in the game's `mods/` directory (`build-mod.ps1` does not copy it; if missing, the mod fails with `ReflectionTypeLoadException`);
- `mod_id.json` / `mod_manifest.json` `dependencies` use the **new object form** with a `min_version` (e.g. `[{"id":"STS2-RitsuLib","min_version":"0.5.18"}]`) to avoid the old-style dependency migration notice.

## Redeploying

After editing code, rebuild and copy into the game — **quit the game first** (`nekospire.dll` is locked by the running process, `Copy-Item` will fail). Use the repo script:
```
game_mod/scripts/build-mod.ps1 -Configuration Release -GameRoot "D:/Steam/steamapps/common/Slay the Spire 2" -GodotExe <path>\Godot_v4.5.1-stable_win64_console.exe
```
(`STS2_DATA_DIR` points at the game data dir; the default C: path causes CS0246.)

## License

This project is licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only).