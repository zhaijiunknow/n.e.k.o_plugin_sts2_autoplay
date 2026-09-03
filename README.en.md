# Plugin Part (Python · NEKO Plugin)

**Location**: repository root (Python package); `plugin.toml` declares the `STS2AutoplayPlugin` entry point.

This plugin handles **decision & expression**: it gives the Slay-the-Spire-2 catgirl companion autoplay (card plays / map movement), situation commentary, a LLM score for event rooms, in-game danmaku, and cross-run memory. It talks to the game through the Mod's HTTP API (`/state`, `/events/stream`, `/action`, `/solver/plan`, `/danmaku`) and does **not** read `strategies/` md itself — that's strategy content, left to `strategy_repository`.

## Entry actions (`__init__.py`)

- **Status**: `sts2_health_check`, `sts2_get_status`, `sts2_read_state`
- **Autoplay**: `sts2_start_autoplay`, `sts2_pause_autoplay`, `sts2_resume_autoplay`, `sts2_stop_autoplay`, `sts2_set_standby`
- **Companion**: `sts2_enable_companion_mode`, `sts2_disable_companion_mode`
- **Co-op**: `sts2_open_coop_room`
- **Strategy**: `sts2_apply_user_override`
- **Planning**: `sts2_get_planned_operation`, `sts2_execute_planned_operation`

## Core modules (by responsibility)

| Module | Responsibility |
|---|---|
| `service.py` | Orchestrator: event-loop wiring, decision / commentary / memory entry points |
| `loop_runner.py` | Polling + SSE event stream + autoplay loop; caches `/solver/plan` (whole turn), event-room LLM |
| `transport_client.py` | Mod HTTP client (`/state`, `/action`, `/solver/plan`, `/danmaku`, SSE) |
| `neko_interface.py` | NEKO SDK interface (read-only / control tool entry points) |
| `heuristic_planner.py` / `companion_evaluator.py` | Decision: situation evaluation, play/card/route heuristics + commentary trigger |
| `catgirl_llm.py` | LLM generation: `CatgirlCommentGenerator` (danmaku / commentary), `EventAdviceGenerator` (event-rank scores) |
| `catgirl_memory.py` | Cross-run memory accumulation (see next section) |
| `strategy_repository.py` / `strategy_parser.py` | Strategy loading: `strategies/` parsing + `strategy_directives` assembly |
| `dispatcher.py` | NEKO output boundary for status feedback / companion notifications |

## Decision & Expression

- **Decision**: map / reward / deck / event use heuristics + (event-room) LLM-score fusion; combat card plays are decided authoritatively by `/solver/plan`, falling back to heuristics.
- **Expression**: catgirl commentary + in-game danmaku.
- **Preference capture**: `sts2_apply_user_override` turns the user's natural-language guidance into structured preferences / overrides.
- **Cross-run memory**: see next section.

## Catgirl Memory Growth (cross-run lessons → decision)

When a run ends (death / clear — the `state_machine` classifies it as `screen_class=terminal`), the plugin summarizes the run's `recent_decision_memory` into "lessons + preferences", writes them via `CatgirlMemoryStore` in `catgirl_memory.py`, and persists to `<LOCALAPPDATA>/N.E.K.O/sts2_autoplay/catgirl_memory.json`. On the next run, `strategy_repository.build_context()` merges the recent runs' lessons into `strategy_directives.avoid` and preferences into `prefer` (soft constraints), thereby influencing card / route / combat decisions. The summarizer is **heuristic-first with optional LLM** (added only when `service._catgirl_llm.available`; silently falls back to heuristics on failure).

## Danmaku / Commentary

- Catgirl commentary goes through the plugin's `_maybe_emit_catgirl_llm` → `_push_catgirl` → `POST /danmaku` (text + avatar) for in-game rendering; commentary no longer goes through the NEKO host `push_message`.
- In combat the LLM prompt is trimmed to "monster HP + our HP + current floor"; only when SSE `event_type == player_action_window_opened` (it's the player's turn) is the current-turn play line appended (`_build_combat_prompt`, reusing `loop_runner._solver_plan_cached`; on a signature hit it doesn't re-run `/solver/plan`).
- Danmaku responds only to three combat events: `combat_started` / `available_actions_changed` / `combat_turn_changed`, of which only `combat_turn_changed` (turn advance) carries the line.

## Data flow

```
Game combat / scene changes
  → Mod (C#) samples state, pushes /events/stream
  → plugin loop_runner consumes
  → heuristic_planner / companion_evaluator make decisions
  → (optionally) catgirl_llm generates danmaku / commentary
  → Mod /action executes → /danmaku renders danmaku
  → (run end) catgirl_memory summarizes lessons / preferences → persisted → next run injects into strategy_directives
```

## Getting Started

### Get the MOD

1. Subscribe and enable the prerequisite mod **STS2-RitsuLib** (Workshop `3747602295`; it's recommended to declare a `min_version` in `mod_id.json`).
2. Subscribe to **猫娘尖塔 NekoSpire** on the [Workshop](https://steamcommunity.com/sharedfiles/filedetails/?id=3794941932).

### Start the plugin

1. In NEKO, enable Paw (猫爪) + this plugin. On connect the plugin sends `POST /config` to turn off the Mod's own LLM/danmaku, handing commentary over to the NEKO-side catgirl.
2. Use the plugin's `sts2_start_autoplay` etc. to control autoplay, and `sts2_open_coop_room` to open a co-op room.


# Mod Part (C# · in-game Godot Mod)

**Location**: `game_mod/nekospire/`, compiled into `nekospire.dll` loaded with the game. Listens on **18080** by default (the `STS2_API_PORT` env var can change it).

This Mod (NekoSpire) is the catgirl companion bridge for StS2: it embeds the **Combat Solver** combat brain, exposes game state and combat routes to the NEKO plugin over HTTP, and ships in-game danmaku rendering plus the low-level co-op autoplay driver. It only provides **executable** capability (card plays / state sampling, solver decisions, danmaku rendering, local autoplay) and does **not** read `strategies/` md — that's the plugin's content.

## Main features

**Cross-turn combat solving (`GET /solver/plan`)**: embeds the **CombatSolver search/simulation core**, not just the current turn — it keeps predicting draws, reshuffles, enemy actions, and later resources. It captures the **live** Play-phase state on the game thread (the current turn's real hand), groups the predicted route by turn, and the current turn's `card_index` lands directly on the real hand position.

**Co-op & independent-player driving (`NekoAutoplayDriver`)**: the catgirl as an independent player = two processes, each with its own Mod-driven local player. Combat uses the next step of `/solver/plan`; map / reward / card / event screens ask the LLM first, falling back to per-screen heuristics when the LLM is unavailable or returns an illegal action. Every candidate is gated by the game's own `available_actions` — it never issues an illegal action. Non-client / non-co-op processes stay quiet; the host is undisturbed.

**In-game danmaku overlay (`POST /danmaku`)**: the catgirl commentary renders directly in-game (built-in Godot nodes + `ProcessFrame` signal, not `_Process`), with configurable font size and avatar; danmaku is decoupled from the decision LLM, with its own `danmaku_enabled` toggle.

**HTTP state bridge (`/state`, `/events/stream`)**: exports a complete snapshot on demand; screen transitions + combat state changes are pushed in real time over SSE, driven by actual game transitions rather than pure polling.

## Standalone run: co-op startup flow

When running without the NEKO plugin and with the Mod configured with its own LLM, the catgirl's co-op as an independent player is driven by the Mod itself. **Two-process model**: one is the host (human playing manually), the other the catgirl client (autoplay). Below is the main in-game one-click flow, plus two launcher/Steam paths.

### Option 1: in-game one-click room (recommended · production UI)

1. **Configure the LLM.** Main menu → Settings → Manage MODs → select NekoSpire → open NekoSpire settings (the "NekoSpire Settings" button on the Mod details page) → fill `Base URL` / `API Key` / `Model`, check "Enable LLM decisions (map/rewards/events/deck decided by LLM)", optionally check "Enable catgirl danmaku commentary" → Save. This is what the catgirl uses for decisions and danmaku commentary.
2. **Open a room from the main menu.** Click "Start co-op room". The Mod then:
   - Sets `coop_enabled = true` and saves (`user://NekoSpire/settings.json`);
   - Closes the main-menu submenu, then `open_multiplayer_menu` → `start_multiplayer_host` (ENet `33771`);
   - **Launches a second game process** (the catgirl client) with `STS2_API_PORT=<coop_client_port·default 18081>`, `STS2_ENABLE_DEBUG_ACTIONS=1` (same exe, **without** `+connect_lobby`).
3. **The catgirl process takes over.** It reads the same `settings.json`; if its process port == `coop_client_port` → `IsClientByPort()` identifies it as the catgirl, and `NekoAutoplayDriver` starts: main menu → `open_multiplayer_menu` → `join_multiplayer_direct` (connecting to host `127.0.0.1:33771`) → lobby selects **IRONCLAD** (roster 0) + ready → start → map votes.
4. **The host plays manually; the catgirl follows.** Combat uses the next step of `/solver/plan`; map / reward / card / event screens ask the LLM (configured in step 1) first, falling back to per-screen heuristics when the LLM is unavailable or returns an illegal action; every candidate is gated by the game's own `available_actions` — never an illegal action.
5. **Danmaku commentary renders only on the host process** (`NekoDanmakuDriver` uses `IsCatgirlProcess()` to skip on the catgirl process), so the catgirl process focuses on being a teammate rather than spamming danmaku.

### Option 2: launcher script `Start-NekoCoop.ps1` (debug / automation)

Reuses `start-game-session.ps1` to launch host + client instances (different API ports, both debug), then drives the session over HTTP `/action`. It defaults to the "multiplayer test" debug scene; `-ClientAutoplays` makes the catgirl client join/select/ready/vote on its own; `-EnterCombat` makes both vote the same node into a first combat and wait for `COMBAT`.

```powershell
scripts\Start-NekoCoop.ps1 -ClientAutoplays -EnterCombat              # fully automatic
scripts\Start-NekoCoop.ps1 -SkipHostLaunch -ClientAutoplays           # host already launched manually from Steam
```

### Two-process notes

- **Who counts as the catgirl**: this process's `STS2_API_PORT` (or `STS2_COOP_PORT` override) == `NekoConfig.coop_client_port` (default 18081); or `STS2_CONNECT_LOBBY` is set. All other instances are treated as players and stay manual / quiet.
- **Shared config**: host and catgirl are the same install, the same `user://NekoSpire/settings.json`; the `coop_enabled` the host sets is also read by the catgirl.
- **Never hijack the host**: if `multiplayer_lobby.is_host` or `net_game_type == "host"` is detected at runtime, autoplay is immediately disabled with a warning (a guard against mis-wiring).


## Dependencies and compatibility

- Requires **RitsuLib 0.5.18+**; currently adapted to 《Killing the Spire 2》 0.111.0 (net9.0 / C# 13).
- The search is bounded by Short/Deep time budgets and branch limits; what's shown is the best route found within the budget, not a mathematical global optimum; **it does not modify the game's RNG**.
- Card-effect inference depends on RitsuLib (eliminating `unsupported` card models); private game members are accessed via **GameRef reflection** (no compile-time/runtime publicization, no Krafs.Publicizer).

## Performance notes

`/solver/plan` is a short-budget search (default Short 8s / Deep 120s; Deep only triggers when Short can't solve it), running in the background on worker threads and periodically yielding the CPU. The self-built `Game/Sim/` deterministic simulator is only a fallback when `SolverEnabled=false`; when the solver is unavailable, autoplay falls back to "the first playable card" to avoid deadlock.

## Architecture & HTTP endpoints

- **Entry `ModEntry.cs`**: starts `GameThread`, `GameEventService` + `ScreenEventBridge`, `HttpServer`/`Router`, `CombatSolverRuntime`, `NekoConfig`, `NekoDanmakuDriver`, `NekoAutoplayDriver` in order.
- **HTTP routes** (`Server/Router.cs`):

| Route | Purpose |
|---|---|
| `/health` | handshake / version |
| `/state` | full snapshot |
| `/coop/state` | co-op read view (per-player action phase + hand + shared enemies) |
| `/action` / `/actions/available` | execute action / available actions |
| `/config` / `/config/open` | config read/write (the plugin turns off the Mod's LLM/danmaku after connecting) |
| `/danmaku` | in-game danmaku POST |
| `/events/stream` | SSE scene-event stream |
| `/solver/plan` | combat solver decision (authoritative source) |

- **Combat solver** (`Solver/`): embeds the **vendored CombatSolver 0.28 engine**, card-effect inference via **RitsuLib**, private members via **GameRef reflection** (Krafs.Publicizer removed). `Game/Sim/` is an independently reimplemented deterministic simulator, used only as a fallback.

## Bug reporting

For wrong routes, unexpected recomputation, or execution errors, send the game log and NEKO log to the author's email (see the "Contact" section at the bottom).

## Setting the MOD port

The Mod listens on 18080 by default; although an uncommon port, it can still be occupied.

Create an environment variable (in global variables) named `STS2_API_PORT` and set it to any unoccupied port.

Example: variable name `STS2_API_PORT`, value `38080`.

## Third-Party Credits

This Mod vendors and adapts the combat-solver core of the following third-party projects:

- **[Combat Solver](https://github.com/Torch1230/CombatSolver)** (author: **Torch / Torch1230**) — [Workshop page](https://steamcommunity.com/sharedfiles/filedetails/?id=3790899961). Combat route solver. Its **search/simulation core** is embedded and adapted here to drive `GET /solver/plan` combat recommendations. Used with the author's permission.
  - Card-effect inference uses **RitsuLib**'s `HarmonyIl` / `RitsuLibFramework.GetMaxHandSize` / `IComputedDynamicVar` (higher fidelity: no more `unsupported` card models).
  - The engine's direct access to private game members is routed through **`GameRef`** reflection (the port does not rely on compile-time/run-time publicization).
  - **`Krafs.Publicizer` is not used** (pure `GameRef` reflection is sufficient to build & run).
- **[Random Foreseer](https://github.com/hotwords123/StS2.RandomForeseer)** (author: **hotwords123**) — [Workshop page](https://steamcommunity.com/sharedfiles/filedetails/?id=3747531952). Combat Solver's combat-simulation engine core (combat state, piles, RNG, fork, history, mirror) derives partly from this, reworked under hotwords123's written permission. This Mod vendors the engine source only and does **not** load/distribute the Random Foreseer assembly as a runtime dependency.

> Third-party source follows the **original authors' licensing boundaries**: Combat Solver does not ship a unified repository license; Random Foreseer-derived code is governed by Combat Solver's `THIRD_PARTY_NOTICES.md`. The above is not automatically covered by this repository's AGPL-3.0-only.

This Mod's MCP module reference:

- **[STS2-Agent](https://github.com/CharTyr/STS2-Agent)** (author: **杉茶 / CharTyr**) — an AI Agent Mod. Configure model endpoints in a visual window, chat, adjust thinking strength per model, and let the model play automatically. This MOD didn't heavily modify the specific data source.

This Mod's danmaku module references:

- **[danmuai](https://github.com/PEPETII/danmuai)** (author: **timerome / PEPETII**) — give screen content its own AI danmaku. The catgirl-danmaku feature was born after seeing this repo, hence the acknowledgements.

- **[弹幕尖塔 DanmakuSpire]** (author: **Icnsis**) — [Workshop page](https://steamcommunity.com/sharedfiles/filedetails/?id=3779807977). This MOD deliberately tuned the visual style after DanmakuSpire, while the code itself was written independently. His style was also great, so it's acknowledged.

**Thanks to open source**, you absolute stitched-together monster.

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

## Contact

Send the game log and NEKO log to zhaijiunknown@outlook.com for any issues.

Game log
```text
%AppData%\SlayTheSpire2\logs
```

NEKO log
```text
Your user folder\AppData\Local\N.E.K.O\logs
```

## License

This project is licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only).
