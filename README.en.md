# Plugin Part (Python · NEKO Plugin)

**Location**: repository root (Python package); `plugin.toml` declares the `STS2AutoplayPlugin` entry point.

This plugin handles **decision & expression**: it gives the Slay-the-Spire-2 catgirl companion autoplay (card plays / map movement), situation commentary, in-game danmaku, and cross-run memory. It talks to the game through the Mod's HTTP API (`/state`, `/events/stream`, `/action`, `/solver/plan`, `/policy/decision`, `/danmaku`) and does **not** read `strategies/` md itself — that's strategy content, left to `strategy_repository`.

Autoplay has two decision sources, chosen automatically by **the character of the current run** (nothing to configure):

| Character of the run | Who decides |
|---|---|
| model downloaded | the **policy network** (AlphaDefect) decides every step; a step it cannot produce falls back to the next row |
| no model downloaded | combat by **CombatSolver** (`/solver/plan`), everything else by **heuristics** |

Models are downloaded per character on the plugin panel's "策略网络" (policy network) page; see [Policy network](#policy-network-alphadefect) below.

## Entry actions (`__init__.py`)

- **Status**: `sts2_health_check`, `sts2_get_status`, `sts2_read_state`
- **Autoplay**: `sts2_start_autoplay`, `sts2_pause_autoplay`, `sts2_resume_autoplay`, `sts2_stop_autoplay`, `sts2_set_standby`
- **Companion**: `sts2_enable_companion_mode`, `sts2_disable_companion_mode`
- **Danmaku**: `sts2_set_danmaku` (`enabled=true/false`) — the real master switch; turning it off also tells the mod to stop rendering and clears the overlay
- **Save**: `sts2_continue_run` — clicks Continue on the main menu to get back into the saved run
- **Co-op**: `sts2_open_coop_room`
- **Strategy**: `sts2_apply_user_override`
- **Planning**: `sts2_get_planned_operation`, `sts2_execute_planned_operation`
- **Policy-network download page**: `sts2_policy_status`, `sts2_policy_prepare_runtime`, `sts2_policy_download_model`, `sts2_policy_remove_model` (used only by `static/policy.html`; not exposed to the LLM as tools)

## Core modules (by responsibility)

| Module | Responsibility |
|---|---|
| `service.py` | Orchestrator: event-loop wiring, decision / commentary / memory entry points |
| `loop_runner.py` | Polling + SSE event stream + autoplay loop; caches `/solver/plan` (whole turn), event-room LLM |
| `transport_client.py` | Mod HTTP client (`/state`, `/action`, `/solver/plan`, `/danmaku`, SSE) |
| `neko_interface.py` | NEKO SDK interface (read-only / control tool entry points) |
| `heuristic_planner.py` / `companion_evaluator.py` | Decision: situation evaluation, play/card/route heuristics + commentary trigger |
| `policy_client.py` | Policy-network wiring: ask the Mod for candidates + observation frame → ask the inference sidecar to score → have the Mod execute; returns nothing when it cannot decide, and the heuristics above take over |
| `policy_assets.py` / `policy_manifest.json` | Local assets of the policy network: download, verification and installation of the runtime and per-character models (the manifest pins every file's size and SHA-256) |
| `policy_sidecar/` | The inference sidecar process (`alphadefect_server.py`) and the inference subset of upstream's Python (`upstream/`, MIT) |
| `catgirl_llm.py` | LLM generation: `CatgirlCommentGenerator` (danmaku / commentary **text**). Text only — no decisions |
| `play_style.py` | Play styles (serious / mischievous / silly): constants, aliases, and the pure "pick one from an ordered candidate list" function |
| `catgirl_memory.py` | Cross-run memory accumulation (see next section) |
| `strategy_repository.py` / `strategy_parser.py` | Strategy loading: `strategies/` parsing + `strategy_directives` assembly |
| `dispatcher.py` | NEKO output boundary for status feedback / companion notifications |

## Decision & Expression

- **Decision**: when the character of the run has a downloaded model, every step is decided by the **policy network** (next section). Otherwise — and for any step the network cannot produce — map / reward / deck / event are decided by **heuristics**, and combat card plays are decided authoritatively by `/solver/plan`, falling back to heuristics. The plugin makes no LLM-driven decisions (the event-room LLM-score fusion was removed in 2026-10). The remaining bullets of this section describe the heuristic path.
- **Multi-card selection**: on a chooser that wants 2+ cards, both the plugin and the catgirl driver advance to the next card they have **not** pressed yet on this screen (the pressed `option_index` set is remembered and reset when the screen's candidate set changes), and prefer `confirm_selection` when the game says it can confirm. Both used to send `option_index=0` forever, so the second card was never picked, `max_select` was never reached and the screen hung.
- **Other out-of-combat choosers**: `bundle_selection` (choose then confirm), `capstone_selection` (sweep options, never re-press the same button — index 0 may be Back), `cards_view` (close), `modal` (dismiss before confirm), `game_over` (back to main menu) are all covered now instead of falling into `unknown` and stalling silently.
- **Rewards**: the card-reward chooser now takes a card first (`resolve_rewards` used to be checked first, silently draining every card reward).
- **Known gap**: the out-of-combat **relic** chooser (`NChooseARelicSelection`, e.g. Jewelry Box) has no mod action at all and still needs a manual pick.
- **Expression**: catgirl commentary + in-game danmaku.
- **Preference capture**: `sts2_apply_user_override` turns the user's natural-language guidance into structured preferences / overrides.
- **Cross-run memory**: see next section.

## Policy network (AlphaDefect)

Once a character's model is downloaded, **every step** of that character's autoplay (card plays, card picks, route, shop, events, rest sites) is first handed to the [AlphaDefect](https://github.com/hmty-CHEN/AlphaDefect-Public) network; a step it cannot produce falls back to CombatSolver + heuristics, and `[sts2_policy]` log lines say what was chosen and why a step fell back. **Characters without a downloaded model play exactly as before** (CombatSolver + heuristics).

### How to use it: plugin panel → "策略网络"

There are no paths to configure. Open the "策略网络" page of the plugin panel (`static/policy.html`):

- **Runtime** (Python and PyTorch): checked once when the page opens; if it is not on this machine the download starts automatically (about 154 MB to download, about 600 MB installed). **Nothing is downloaded when the plugin starts.**
- **Character cards**: one per character. Grey = not downloaded; click to download (about 75 MB each). A downloaded card lights up and that character is handed to the network. A model can be removed, which returns the character to CombatSolver + heuristics.
- Files live in `%LOCALAPPDATA%\N.E.K.O\sts2_autoplay\policy_runtime`. Every file is checked against a pinned size and SHA-256, downloads resume after an interruption, and mirrors reachable from mainland China are tried first.

Character select: when some characters have a downloaded model, each run picks at random among them; when none has, the game's own "random" entry is used as before.

### How it runs

- **Three hops**: the Mod's `GET /policy/decision` returns the candidates and an observation frame → the inference sidecar `policy_sidecar/alphadefect_server.py` scores them (its own process, using the downloaded runtime; started by the plugin the first time it is needed) → the Mod executes the chosen candidate (`policy_act`).
- **The vocabulary is not shipped with the Mod**: it is downloaded together with the runtime, and the plugin hands it to the Mod when the Mod has none (or a different one).
- **Multiplayer runs also use the network**, observing from the local player. The network was trained on single-player runs only; there is no data on how it plays in multiplayer. Map votes still follow `map_policy` (not handed to the network unless it is `auto`).
- **Screens that fall back**: card bundles, the Crystal Sphere minigame, multi-card deck selections, "skip the relic" at a chest, and cancelling a card selection have no adapter in the Mod.
- **Reflection does not act on the network**: cross-run lessons only influence the heuristics used on fallback. The network's weights are fixed and do not change with play.
- `policy_enabled = false` (`plugin.toml`) turns the whole thing off.

### How far it has been tested

- **Encoding fidelity**: the frames the Mod produces are compared byte for byte with upstream's headless engine over whole network-driven runs (`game_mod/PolicyFidelity`). Combat, card selection, rewards, events, rest sites, shops and map decisions all match; Crystal Sphere frames and shop frames with a recorded failed purchase are not compared.
- **In the game (2026-11-24, Ascension 10, single player)**: Ironclad won 3 of 4 runs; Necrobinder won 1 of 2 (the other died at the Act 3 boss). That is far too few runs to be a win rate — it shows the pipeline works. The upstream paper reports Ascension-10 greedy win rates of 62%–77% depending on the character.
- **Not run in the game**: the Silent, Defect, Regent and Fool models; real multiplayer.

## Catgirl Memory Growth (cross-run lessons → decision)

When a run ends (death / clear — the `state_machine` classifies it as `screen_class=terminal`), the plugin summarizes the run's `recent_decision_memory` into "lessons + preferences", writes them via `CatgirlMemoryStore` in `catgirl_memory.py`, and persists to `<LOCALAPPDATA>/N.E.K.O/sts2_autoplay/catgirl_memory.json`. On the next run, `strategy_repository.build_context()` merges the recent runs' lessons into `strategy_directives.avoid` and preferences into `prefer` (soft constraints), thereby influencing card / route / combat decisions. The summarizer is **heuristic-first with optional LLM** (added only when `service._catgirl_llm.available`; silently falls back to heuristics on failure).

## Danmaku / Commentary

- Catgirl commentary goes through the plugin's `_maybe_emit_catgirl_llm` → `_push_catgirl` → `POST /danmaku` (text + avatar) for in-game rendering; commentary no longer goes through the NEKO host `push_message`.
- In combat the LLM prompt is trimmed to "monster HP + our HP + current floor"; only when SSE `event_type == player_action_window_opened` (it's the player's turn) is the current-turn play line appended (`_build_combat_prompt`, reusing `loop_runner._solver_plan_cached`; on a signature hit it doesn't re-run `/solver/plan`).
- Danmaku responds only to three combat events: `combat_started` / `available_actions_changed` / `combat_turn_changed`, of which only `combat_turn_changed` (turn advance) carries the line.

### How to actually turn danmaku off (since v0.3.1)

`catgirl_llm_enabled=false` is **not** the danmaku switch — it only downgrades LLM commentary to heuristic text, and danmaku keeps being pushed. Use one of these instead:

| Way | Effect |
|---|---|
| `sts2_set_danmaku(enabled=false)` | Plugin-side master switch: `_push_catgirl` returns immediately (no generation, no avatar fetch, no push) and it also sends `POST /config {danmaku_push_enabled:false}` + `POST /danmaku/clear` |
| `plugin.toml [sts2] danmaku_enabled = false` | Same, applied at plugin startup |
| Uncheck "allow in-game danmaku rendering" in the in-game Mod settings | Turns off the mod-side `danmaku_push_enabled`: nothing any local caller pushes is rendered, and the overlay is cleared |

Three separate switches: `danmaku_enabled` (plugin pushes), `danmaku_push_enabled` (mod overlay rendering, funnelled through `DanmakuService.PushAsync`), `catgirl_llm_enabled` (LLM vs heuristic commentary text). Autoplay and state reads are unaffected by all three.

## Map votes & saves (co-op behaviour)

**First, the two run modes behave differently:**

| Mode | How it is detected | Map behaviour |
|---|---|---|
| **Plugin-controlled** (NEKO attached) | the mod received a request carrying `X-Neko-External-Control: 1` within the **30s lease** | follows `coop_map_policy` (below) |
| **Mod standalone** (NEKO absent) | lease expired (at most 30s after the plugin exits/crashes) | **plays on its own, never blocks**: mirrors a teammate's vote if there is one, otherwise waits `coop_map_vote_wait_seconds` and then picks |

The plugin attaches that header to every request (including the SSE stream) and polls `/state` every 3s idle / 1s active, so the header doubles as a heartbeat: if the plugin dies the mod falls back to standalone by itself and **never gets stuck**. `/state` echoes `external_control: {attached, client_id, seconds_since_contact, lease_seconds}` for diagnosis.

**`coop_map_policy` (mod) / `map_policy` (plugin) under plugin control:**

- **`wait` (default)** — hold completely: in a multiplayer run the catgirl neither votes nor auto-follows, leaving the route entirely to the player until the player speaks to it. That is the "stop stealing the route" behaviour. Single-player maps still self-pick, otherwise every node would idle a turn for nothing.
- **`follow`** — mirror the teammate's vote once they vote; when nobody voted, wait `map_vote_wait_seconds` (default 20s) and then self-pick.
- **`auto`** — no waiting, pick immediately.

**Talking to the catgirl about it (`sts2_set_route`, also an `llm_tool`):**

| You say | mode | Behaviour |
|---|---|---|
| "decide the route" / "you pick" / "pick a safe one" | `safe` | scores a conservative node itself and votes |
| "I picked, take this one" / "same as me" | `mirror` | votes your current vote |
| "take the middle" / "the third one" / "leftmost" | `pick` | votes the position you named |
| "no no I picked wrong, you take the middle" | `pick` | **call it again to change the vote** |

Note: `safe` is a **heuristic** conservative score (avoid elites, prefer rests below 60% HP), **not** a real battle-loss forecast; and changing a vote only works before the map advances. Legacy configs that only set `map_follow_player` are still understood (`true`→follow, `false`→auto).

- **Back to the save**: when the main menu has a resumable run, the plugin's main-menu decision now **prefers `continue_run`** (it used to prefer `open_character_select`, which started a new run ahead of the save), and the catgirl process **never touches the save** (both processes share `user://`, so whoever clicks Continue consumes it) — it only keeps the ability to rejoin a room. `sts2_continue_run` is the explicit recovery entry point, and `continue_run` now pops any open main-menu submenu first (an open submenu hides the Continue button, which previously just returned 409).
- **Guard**: with a resumable save present, `start_coop_session` / `start_multiplayer_host` return `409 active_run_present` instead of silently starting a new run (`POST /config {coop_yield_to_saved_run:false}` disables this guard).
- **Known limitation**: the game appears to have no join-in-progress. Even if the host resumes the co-op save, the AI teammate may not get back into that run — a game-side capability boundary, not something the plugin can work around.

## Control modes (play on your own / listen / stop)

Switch with `sts2_set_control_mode`; the catgirl also calls it itself when you say so:

| Mode | You say | Behaviour |
|---|---|---|
| `program` (**default**) | "play on your own", "just play" | decides and executes as usual — identical to today |
| `advise` | "listen to me", "hold off", "wait for me" | **computes but does not act**: outside combat it waits for your go on every decision screen |
| `standby` | "stop", "standby" | stops entirely, not even planning |

**Switching is silent.** A mode change does not make the catgirl speak (the push uses `ai_behavior="blind"` — it is not even fed to the LLM, only kept as an internal record for the panel and for diagnosis), and the `llm_tool` returns a terse machine result (`{"ok": true, "style": ...}`) — a sentence there would just get parroted back. So she will **not** announce "ok, I'm on mischief mode now"; she just carries on, and only answers if you ask which mode she is in.

She may also **switch on her own judgement** without being told: your tone is enough ("why are you so dumb today" -> mischief or silly; "play properly" -> serious; "I'll call this one" -> listen). The tool descriptions explicitly authorise that.

`advise` is driven by the entry pair that already exists:

- "what do you plan to do" -> `sts2_get_planned_operation` speaks the plan
- "go with that" / "play it" -> `sts2_execute_planned_operation` takes one step (this is **explicit authorisation** and bypasses the hold)
- "play on your own" -> `sts2_start_autoplay`, i.e. back to `program`

**The hold has hard boundaries — each one would deadlock the game:**

- **Never holds in combat** — the player cannot play the catgirl's cards, so holding would stall the turn forever. `advise` therefore only covers the "slow" decisions: map, rewards, card picks, relics, chests, events, shop, rest, bundles, capstones.
- **Never holds on the main menu / lobby / character select** — that is how the catgirl joins the room (menu -> connect -> pick character -> ready); holding there means it never gets into the run at all. The same is true for `standby`.
- **Never holds on an unrecognised screen** — better to miss an instruction than to deadlock the mod.

Exception: `standby` really does stop everything, so it **includes combat** — the turn stops until you speak. That is the intended semantics of that mode, not a bug.

**The mode is pushed to the mod** (`POST /config {coop_control_mode: auto|advise|hold}`), because the mod has its own autoplay driver: stopping only the plugin is not stopping anything. Mapping: `program→auto`, `advise→advise`, `standby→hold`. Like the map policy, this sync only matters while the **plugin is attached** (see the lease above); with no plugin the mod always runs itself.

## Play style (serious / mischievous / silly)

A different axis from the control mode: the control mode decides **who calls the shots**, the play style decides **how seriously it plays**.

| Style | You say | Behaviour |
|---|---|---|
| `serious` (**default**) | "play it straight", "play properly", "I want to win" | **byte-identical** to before this feature |
| `mischievous` | "mischief", "just for fun", "don't be so serious" | 20% chance to swap the best move for the **runner-up** legal one |
| `silly` | "silly", "you idiot", "just play anything" | 60% chance to swap the best move for the **worst** legal one |

Switch at runtime with `sts2_set_play_style(style=...)`; config key `play_style` (plugin `plugin.toml` / `config.example.toml`).

**Three hard guarantees:**

1. **`serious` has zero impact** — under serious the perturbation layer returns the planner's result verbatim and does not even compute the candidate ranking, so the default style cannot regress.
2. **Always legal** — candidates come only from that screen's **legal index set** (unplayable cards and unaffordable shop items are excluded), and the swapped operation still has to pass `action_engine` validation; if no legal alternative exists the perturbation is abandoned and the best move is kept. It never emits an illegal action.
3. **Every decision screen is covered** — combat plays, map, rewards, card picks (all variants), events, shop, rest, chests, bundles and capstones. Events / map / rewards / card-removal are ranked with each screen's **existing scorer**; the remaining screens degrade to "best + the other legal indices".

Exception: **a step the player explicitly authorised is never perturbed.** `sts2_execute_planned_operation` ("go with that") is explicit authorisation, so it does exactly what you said even in silly mode.

## Which process the plugin is driving (host / catgirl)

This is the easiest thing to get backwards in co-op: **the port does not distinguish a mode, it distinguishes a game process.**

| Port | Who |
|---|---|
| **18080** (`HttpServer.DefaultPort`, overridable with `STS2_API_PORT`) | the only instance in a solo run; **in co-op it is the host (the human's)** |
| **18081** (`NekoConfig.coop_client_port`) | the **catgirl process** the mod launches with `STS2_API_PORT=18081` |

So the plugin has an "active instance", inspectable and overridable with `sts2_set_instance`:

| Value | Meaning |
|---|---|
| `host` | the configured `base_url` (default 18080). **Opening a room must go here** — only the host can launch the catgirl process |
| `catgirl` | the catgirl process. When the plugin drives play, it reads state and sends actions here |

**It is automatic by default**: after `sts2_open_coop_room(host=True)` succeeds, the plugin reads `coop_client_port` from the host's `GET /config`, derives the catgirl address, **polls while it cold-starts** (up to 45s), and switches over once it is up.

On switching it pushes `coop_control_mode="hold"` to that instance so its **own mod driver stands down on every screen except the join flow** — otherwise the two drivers race for the same action. Joining (menu -> lobby -> character select -> ready) is **still done by the mod**, which is the part the plugin cannot reach. If the plugin dies, the lease lapses and the mod resumes playing on its own.

**It falls back**: while the active instance is the catgirl, three consecutive read failures switch the plugin back to the host, so it never sticks on a dead address.

Note `coop_client_port` in `GET /config` is a read-only field that only exists from **mod 0.1.4** onwards. With an older mod the plugin **does not guess a port**; it stays on the host instance and says so — solo runs are unaffected.

## Data flow

```
Game combat / scene changes
  → Mod (C#) samples state, pushes /events/stream
  → plugin loop_runner consumes
  → decide: character has a model → policy_client (Mod /policy/decision → inference sidecar → policy_act)
            otherwise, or the network cannot decide → heuristic_planner (combat reads Mod /solver/plan)
  → companion_evaluator triggers commentary
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

### Enable the policy network (optional)

1. Open the plugin panel → "策略网络". The page checks the runtime (Python and PyTorch) and starts downloading it if it is missing (about 154 MB, once).
2. Click the card of each character you want to download its model (about 75 MB each). Grey cards are not downloaded yet.
3. Start autoplay as usual. Character select picks at random among the characters with a model; `[sts2_policy]` log lines say what the network chose at each step.

Skipping this is fine: every character then plays with CombatSolver + heuristics, as before.


# Mod Part (C# · in-game Godot Mod)

**Location**: `game_mod/nekospire/`, compiled into `nekospire.dll` loaded with the game. Listens on **18080** by default (the `STS2_API_PORT` env var can change it).

This Mod (NekoSpire) is the catgirl companion bridge for StS2: it embeds the **Combat Solver** combat brain, exposes game state and combat routes to the NEKO plugin over HTTP, and ships in-game danmaku rendering plus the low-level co-op autoplay driver. It only provides **executable** capability (card plays / state sampling, solver decisions, danmaku rendering, local autoplay) and does **not** read `strategies/` md — that's the plugin's content.

## Main features

**Cross-turn combat solving (`GET /solver/plan`)**: embeds the **CombatSolver search/simulation core**, not just the current turn — it keeps predicting draws, reshuffles, enemy actions, and later resources. It captures the **live** Play-phase state on the game thread (the current turn's real hand), groups the predicted route by turn, and the current turn's `card_index` lands directly on the real hand position.

**Co-op & independent-player driving (`NekoAutoplayDriver`)**: the catgirl as an independent player = two processes, each with its own Mod-driven local player. Combat uses the next step of `/solver/plan`; map / reward / card / event screens ask the LLM first, falling back to per-screen heuristics when the LLM is unavailable or returns an illegal action. Every candidate is gated by the game's own `available_actions` — it never issues an illegal action. Non-client / non-co-op processes stay quiet; the host is undisturbed.

**In-game danmaku overlay (`POST /danmaku`)**: the catgirl commentary renders directly in-game (built-in Godot nodes + `ProcessFrame` signal, not `_Process`), with configurable font size and avatar; danmaku is decoupled from the decision LLM, with its own `danmaku_enabled` toggle. Every producer funnels through `DanmakuService.PushAsync`, which is runtime-gated by `danmaku_push_enabled` (so anything a local caller pushes is controllable too), and `POST /danmaku/clear` clears the screen.

**HTTP state bridge (`/state`, `/events/stream`)**: exports a complete snapshot on demand; screen transitions + combat state changes are pushed in real time over SSE, driven by actual game transitions rather than pure polling.

**Policy-network endpoints (`GET /policy/decision`, `policy_act`)**: turn the current screen into the input the AlphaDefect network expects — a list of candidate actions plus one binary observation frame — and execute the candidate the network picks. The observation encoder is upstream's own source (`Policy/Vendor`), so the encoding is byte-identical to what the network was trained on; what the Mod adds is reading the candidates off the live game screens and translating a candidate into a game action. The network itself is not in the Mod: inference runs on the NEKO plugin's side, and the Mod ships neither models nor the vocabulary (the plugin sends the vocabulary with `POST /policy/data`). Without the plugin these endpoints are simply unavailable; nothing else is affected.

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

- Requires **RitsuLib 0.6.11+**; currently adapted to 《Killing the Spire 2》 0.113.x (net9.0 / C# 13).
- The policy network's vocabulary and models are pinned to the version upstream released for game **v0.113.1**. Cards or relics added by a later game update are encoded as "unknown": the network still runs but knows nothing about them. Following a game update requires a new upstream release, an updated `policy_manifest.json` and a re-vendored encoder.
- The search is bounded by a think-time ceiling (`solver_budget_seconds`, default 12 s, only reached on hard fights) and node/branch limits; what's shown is the best route found within the budget, not a mathematical global optimum; **it does not modify the game's RNG**.
- The solver supports **vanilla content only**: when the position contains a third-party mod's card/relic/monster/character it refuses explicitly (`in_combat=false` plus the reason) instead of guessing. In multiplayer (co-op) combat it plans for the **local player** only: the player-count scaling of enemy HP/block is modelled, teammates are not (the line assumes they do nothing further; the payload carries a `multiplayer:teammates_not_modeled` warning and `coverage.risk=true`).
- Private game members are accessed via **GameRef reflection** (no compile-time/runtime publicization, no Krafs.Publicizer); RitsuLib remains a hard dependency (the engine reads `RitsuLibFramework.GetMaxHandSize`).

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
| `/danmaku` | in-game danmaku POST (gated by `danmaku_push_enabled`) |
| `/danmaku/clear` | clear on-screen danmaku and detach the overlay |
| `/events/stream` | SSE scene-event stream |
| `/solver/plan` | combat solver decision (authoritative source) |
| `/policy/decision` | policy network: candidates + observation frame for the current screen (`?obs_format=N` selects the frame format); says why when it is not its turn or not its screen |
| `/policy/data` | policy network: receives the vocabulary from the plugin (stored in `user://NekoSpire/policy`) |

The `/action` that goes with the policy network is `policy_act`: it carries the `decision_id` returned by `/policy/decision` and a candidate index, and the Mod executes it only after checking that the screen has not changed.

- **Combat solver** (`Solver/`): embeds the **vendored CombatSolver 1.0.0 engine** (upstream `main@1ca3d6ce`: HotKernel pure-value combat kernel + HotSearch; plus nekospire's own local-player patch for multiplayer combat — upstream itself is untouched), private members via **GameRef reflection** (no Krafs.Publicizer). `Game/Sim/` is an independently reimplemented deterministic simulator, used only as a fallback (`SolverEnabled=false`).
  - The vendoring boundary, adaptation rules and re-sync procedure are documented in [`game_mod/nekospire/Solver/VENDORING.md`](game_mod/nekospire/Solver/VENDORING.md). Pipeline: `game_mod/scripts/vendor-combat-solver.py` (take upstream + rewrite private access), `game_mod/scripts/build-errors.py` (compiler error summary), `game_mod/SolverSmoke/` (offline smoke test, no game process).
- **Policy-network endpoints** (`Policy/`):
  - `Policy/Vendor/` is upstream AlphaDefect's observation encoder source (MIT), regenerated from an upstream checkout by `game_mod/scripts/vendor-alphadefect.py` — **do not hand-edit**; nekospire's changes (observer = local player, etc.) are replayed by that script.
  - `PolicyDecisionBuilder.cs` assembles the candidates of each decision kind by the rules of upstream's headless engine (logic only); `PolicyService.cs` reads the live Godot screens and maps candidates to existing nekospire actions; `PolicyHooks.cs` holds the few read-only hooks the encoder needs.
  - Offline check: `game_mod/PolicyFidelity/` hosts upstream's headless engine and the built `nekospire.dll` in one process; for every decision the network makes, both encode it and the frames are compared byte for byte (`game_mod/scripts/policy_fidelity_run.py`). It checks the logic layer; the screen-reading layer in `PolicyService.cs` has no screens to read offline and can only be verified in the game.

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

> **Licensing boundary**: Combat Solver is released upstream under the **MIT License**, with the notices
> `Copyright (c) 2026 Torch1230 and contributors` and
> `Copyright (c) 2026 hotwords123 (portions derived from Random Foreseer)`.
> MIT permits use, modification, redistribution and sublicensing **provided the copyright notice and the
> licence text are retained**. This repository embeds a subset of that search/simulation core (with
> reflection adaptation and Godot decoupling); the boundary and sync mechanism live in
> [`game_mod/nekospire/Solver/VENDORING.md`](game_mod/nekospire/Solver/VENDORING.md).
> The above is not automatically covered by this repository's AGPL-3.0-only; those portions follow the
> original authors' MIT terms.

The **policy network** used by this Mod and plugin comes from:

- **[AlphaDefect](https://github.com/hmty-CHEN/AlphaDefect-Public)** (author: **Qiyue Chen / hmty-CHEN**) — paper [arXiv:2611.31076](https://arxiv.org/abs/2611.31076). One network per character makes every decision of a run. This project uses three parts of it, all under the **MIT License** (`Copyright (c) 2026 Qiyue Chen`):
  - the **observation encoder** (C#), embedded in the Mod (`game_mod/nekospire/Policy/Vendor`, licence text included); the only change is that the observer is the local player instead of the first player;
  - the **inference code** (Python: frame parser, batch builder, network), shipped with the plugin (`policy_sidecar/upstream`, licence text included);
  - the **per-character network weights**, which are not redistributed here: the plugin's "策略网络" page downloads them from upstream's GitHub Releases and verifies them.

  The vocabulary and content catalog are game-derived data outside upstream's MIT grant; they are not redistributed here either and are fetched from a pinned upstream commit by the download page.
  The live bridge (connecting the encoder to the real game screens and turning the network's choice into game actions) is this project's own code; upstream's headless engine is used for the offline frame-by-frame check.

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
- `mod_id.json` / `mod_manifest.json` `dependencies` use the **new object form** with a `min_version` (e.g. `[{"id":"STS2-RitsuLib","min_version":"0.6.11"}]`) to avoid the old-style dependency migration notice.

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
