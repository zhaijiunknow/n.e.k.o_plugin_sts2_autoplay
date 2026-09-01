## Third-Party Credits

This Mod vendors and adapts the combat-solver core of the following third-party projects:

- **[Combat Solver](https://github.com/Torch1230/CombatSolver)** (author: **Torch / Torch1230**) — combat route solver. Its search/simulation core is embedded and adapted here (de-RitsuLib, reflection-based, no publicize dependency) to drive `GET /solver/plan` combat recommendations. Used with the author's permission.
- **[Random Foreseer](https://github.com/hotwords123/StS2.RandomForeseer)** (author: **hotwords123**) — [Workshop page](https://steamcommunity.com/sharedfiles/filedetails/?id=3747531952). Combat Solver's combat-simulation engine core (combat state, piles, RNG, fork, history, mirror) derives partly from this, reworked under hotwords123's written permission. This Mod vendors the engine source only and does **not** load/distribute the Random Foreseer assembly as a runtime dependency.

> Third-party source follows the **original authors' licensing boundaries**: Combat Solver does not ship a unified repository license; Random Foreseer-derived code is governed by Combat Solver's `THIRD_PARTY_NOTICES.md`. The above is not automatically covered by this repository's AGPL-3.0-only.

## License

This project is licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only).