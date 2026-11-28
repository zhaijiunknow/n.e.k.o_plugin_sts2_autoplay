// Standalone catgirl co-op autoplay driver for NekoSpire. In the catgirl (client) game process this
// subscribes to GameEventService and, on each wake, re-reads a fresh GameStatePayload and issues at most
// one action through the existing GameActionService (via GameThread.InvokeAsync — the same path Router is
// invoked on, bypassing HTTP). Lightweight decisions: combat uses the mod's /solver/plan; every other
// screen uses a simple forward heuristic so the catgirl never gets stuck and never issues an illegal action
// (each candidate is gated on the game's own available_actions). Silent when coop is off, when this is not
// the client process, or when there is nothing to do — so a normal player/host instance stays quiet.
using System;
using System.Diagnostics;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Godot;
using NekoComm.Server;

namespace NekoComm.Game
{
    internal sealed class NekoAutoplayDriver
    {
        private static readonly Lazy<NekoAutoplayDriver> Lazy = new();
        public static NekoAutoplayDriver Instance => Lazy.Value;

        private const string LogPrefix = "[NekoComm.Autoplay]";

        // The player's default API port (mirrors Server.HttpServer.DefaultPort). The catgirl is whoever's
        // own port equals ResolveCoopClientPort(); every other instance is the player and stays manual.
        private const int DefaultApiPort = 18080;

        // 0.7s: minimum gap between any two fired actions (lets a transition settle and stops the event
        // storm from re-deciding every frame). 2.5s: cooldown after a fired action fails/throws before retry.
        private const double MinActionIntervalSeconds = 0.7;
        private const double FailureCooldownSeconds = 2.5;
        // Fallback re-poll: > MinActionIntervalSeconds so a missed event is re-checked once the throttle clears.
        private const double PollIntervalSeconds = 0.8;

        // LLM decision (MAP / reward / deck / event) tuning. A decision is a short JSON action; give it a
        // bigger budget than a danmaku line. When the LLM explicitly says "nothing to do" wait this long to
        // re-ask instead of spamming the API every poll tick.
        private const int DecisionMaxTokens = 220;
        private const double DecisionTemperature = 0.5;
        private const double LlmWaitCooldownSeconds = 8;
        // After this many consecutive solver-unusable ticks inside a combat, degrade to the first playable
        // card instead of waiting — a genuinely broken solver must not stall the catgirl forever, but a
        // transient "not my Play phase yet" refusal should just wait for the right window.
        private const int CombatSolverFallbackThreshold = 8;
        // How many capstone buttons to try before giving up and waiting (index 0 may be a Back/Close button).
        private const int MaxCapstoneSweep = 8;

        private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = false };

        private readonly object _gate = new();
        private bool _started;
        private volatile bool _inFlight;
        private bool _openedMultiplayerMenu;
        private bool _runEnded;
        private volatile bool _coopRejoinPending;
        private int _coopSessionGeneration;
        private double _lastActionAt;
        private double _cooldownUntil;
        private int _combatSolverFails;

        // Map-vote follow-player state. Keyed on the map identity so a new map (or a new room) restarts the
        // "give the human first refusal" countdown instead of inheriting a stale one.
        private string _mapWaitKey = "";
        private double _mapWaitStartedAt;

        // Card-selection memo. A multi-card selection (e.g. "choose 2 cards") cannot be driven by always
        // picking option 0: re-picking an already-picked card is a no-op/toggle, so selected_count never
        // reaches min_select and the loop spins forever. Remember the card-id signature we picked against so
        // each tick advances to a different card.
        private string _selectionSignature = "";
        private int _selectionLastPick = -1;
        private int _selectionPicked;

        // Same "never re-press the same option" cursor for the other multi-step choosers (bundle / capstone),
        // where the pick action stays available after it has been applied.
        private string _bundleSignature = "";
        private bool _bundleChosen;
        private string _capstoneSignature = "";
        private int _capstoneLastOption = -1;

        internal async Task PauseForCoopRejoinAsync()
        {
            lock (_gate)
            {
                _coopRejoinPending = true;
                _coopSessionGeneration++;
            }
            var deadline = DateTime.UtcNow.AddSeconds(5);
            while (_inFlight && DateTime.UtcNow < deadline) await Task.Delay(50);
            if (_inFlight)
            {
                _coopRejoinPending = false;
                throw new ApiException(409, "coop_client_busy", "猫娘仍在处理上一动作，请稍后重试。");
            }
        }

        internal bool CoopRejoinPending => _coopRejoinPending;
        internal void CancelCoopRejoin() => _coopRejoinPending = false;
        internal void ResumeAfterCoopRejoin()
        {
            _runEnded = false;
            _openedMultiplayerMenu = false;
            _cooldownUntil = 0;
            _lastActionAt = 0;
            _combatSolverFails = 0;
            _coopRejoinPending = false;
        }

        public void Start()
        {
            lock (_gate)
            {
                if (_started)
                    return;
                _started = true;
            }
            _ = Task.Run(EventLoopAsync);
        }

        private async Task EventLoopAsync()
        {
            try
            {
                var subscription = GameEventService.Instance.Subscribe();
                while (true)
                {
                    await TickAsync();
                    try
                    {
                        // Wake early on events, but also re-poll on a timer: a transition whose event arrives
                        // inside the min-interval throttle gets swallowed (e.g. available_actions_changed right
                        // after open_multiplayer_menu), and if no later event comes the loop would stall the next
                        // step. The poll fallback makes progress on a timer regardless.
                        var readTask = subscription.Reader.ReadAsync().AsTask();
                        var delayTask = Task.Delay((int)(PollIntervalSeconds * 1000));
                        if (await Task.WhenAny(readTask, delayTask) == readTask)
                        {
                            try { await readTask; } catch { }
                        }
                    }
                    catch
                    {
                        break;
                    }
                }
            }
            catch (Exception ex)
            {
                GD.PrintErr($"{LogPrefix} loop stopped: {ex.Message}");
            }
        }

        private async Task TickAsync()
        {
            if (_coopRejoinPending || _inFlight)
                return;

            var generation = _coopSessionGeneration;
            var now = Now();
            if (now < _cooldownUntil)
                return;
            // Global min gap between fired actions: lets a transition settle and stops the event storm
            // (available_actions_changed / screen_changed re-broadcast during one action window) from
            // re-deciding — and re-running the combat solver — on every wake.
            if (now - _lastActionAt < MinActionIntervalSeconds)
                return;

            // Cheap role gate BEFORE the heavier state read so a manual host / non-client process never
            // rebuilds the full snapshot on every poll tick. In Steam-join mode the STS2_CONNECT_LOBBY env is
            // the authoritative "this is the catgirl" signal (set only by the launcher on the client), so the
            // coop_enabled toggle and port match are not required.
            if (!NekoConfig.Current.coop_enabled && !IsSteamJoinMode())
                return;
            if (!IsClientByPort() && !IsSteamJoinMode())
                return;

            GameStatePayload state;
            try
            {
                state = await GameThread.InvokeAsync(GameStateService.BuildStatePayload);
            }
            catch (Exception ex)
            {
                GD.PrintErr($"{LogPrefix} state read failed: {ex.Message}");
                return;
            }

            if (_runEnded)
                return;
            // Release the per-run latches once we are demonstrably back inside a run. Both used to latch for
            // the whole process lifetime: after one GAME_OVER the catgirl refused to act again (line above),
            // and after one visit to the main menu _openedMultiplayerMenu stayed true so the multiplayer
            // submenu could never be reopened — the catgirl just sat at the main menu forever. Resuming a
            // saved run lands on MAP/COMBAT/... , so this is also what lets the catgirl follow a rejoin.
            if (state.screen is not ("MAIN_MENU" or "GAME_OVER" or "UNKNOWN"))
            {
                _runEnded = false;
                _openedMultiplayerMenu = false;
            }
            // Runtime guard: if a lobby/run already tells us we're the host, never self-drive (mis-wired setup).
            if (state.multiplayer_lobby?.is_host == true ||
                string.Equals(state.multiplayer?.net_game_type, "host", StringComparison.OrdinalIgnoreCase))
            {
                GD.PrintErr($"{LogPrefix} port says catgirl client but runtime says host; disabling autoplay");
                return;
            }

            if (ExternalControl.HoldAutoplay(NekoConfig.Current.coop_control_mode, state.screen, ExternalControl.IsActive))
                return;

            // Plugin /action calls are still allowed. Only this autonomous driver yields,
            // so speech/chat windows also work in the catgirl co-op process. Combat stays fast.
            if (NekoConfig.Current.livestream_enabled && !state.in_combat &&
                state.screen is ("REWARD" or "CARD_SELECTION_REWARD" or "SHOP" or "SHOP_SHOW" or
                    "EVENT" or "REST" or "SELECTION" or "CARD_SELECTION" or "CARD_SELECTION_UNUSEFULL" or
                    "CARD_SELECTION_TRANSFORM" or "CARD_SELECTION_REMOVE" or "CARD_SELECTION_DELET" or
                    "BUNDLE_SELECTION" or "CAPSTONE_SELECTION" or "RELIC_SELECTION"))
                return;

            ActionRequest? req;
            try
            {
                req = await DecideAsync(state);
            }
            catch (Exception ex)
            {
                GD.PrintErr($"{LogPrefix} decide failed: {ex.Message}");
                return;
            }

            if (req == null)
                return;
            // Adoption may arrive while DecideAsync awaits the solver/LLM. Drop that stale decision too.
            if (ExternalControl.HoldAutoplay(NekoConfig.Current.coop_control_mode, state.screen, ExternalControl.IsActive))
                return;

            lock (_gate)
            {
                if (_coopRejoinPending || generation != _coopSessionGeneration) return;
                _inFlight = true;
            }
            try
            {
                var resp = await ExecuteActionAsync(req);
                if (resp == null)
                {
                    GD.Print($"{LogPrefix} action '{req.action}' failed; backoff {FailureCooldownSeconds}s");
                    _cooldownUntil = Now() + FailureCooldownSeconds;
                }
                _lastActionAt = Now();
            }
            finally
            {
                _inFlight = false;
            }
        }

        private async Task<ActionResponsePayload?> ExecuteActionAsync(ActionRequest req)
        {
            try
            {
                return await GameThread.InvokeAsync(() => GameActionService.ExecuteAsync(req));
            }
            catch (Exception ex)
            {
                GD.PrintErr($"{LogPrefix} action '{req.action}' threw: {ex.Message}");
                return null;
            }
        }

        // ---- role identification ----------------------------------------------------------------

        private static bool IsClientByPort() =>
            NekoConfig.Current.coop_enabled && ResolveOwnPort() == ResolveCoopClientPort();

        // The launcher sets STS2_CONNECT_LOBBY only on the catgirl process when it is launched with
        // +connect_lobby <lobbyid>; the presence means "join the Steam room via the game's own auto-join".
        private static bool IsSteamJoinMode() =>
            !string.IsNullOrWhiteSpace(System.Environment.GetEnvironmentVariable("STS2_CONNECT_LOBBY"));

        // Shared role detection: true when this process is the catgirl (co-op autoplay client). Other
        // subsystems (e.g. the danmaku driver) call this to skip work a human-facing instance should own.
        internal static bool IsCatgirlProcess() => IsSteamJoinMode() || IsClientByPort();

        private static int ResolveOwnPort()
        {
            var raw = System.Environment.GetEnvironmentVariable("STS2_API_PORT");
            if (!string.IsNullOrWhiteSpace(raw) && int.TryParse(raw.Trim(), out var p) && p > 0)
                return p;
            return DefaultApiPort;
        }

        private static int ResolveCoopClientPort()
        {
            var raw = System.Environment.GetEnvironmentVariable("STS2_COOP_PORT");
            if (!string.IsNullOrWhiteSpace(raw) && int.TryParse(raw.Trim(), out var p) && p > 0)
                return p;
            return NekoConfig.Current.coop_client_port;
        }

        private static double Now() => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;

        // ---- per-screen decisions ---------------------------------------------------------------

        private async Task<ActionRequest?> DecideAsync(GameStatePayload s)
        {
            switch (s.screen)
            {
                case "MAIN_MENU":
                    return DecideMainMenu(s);
                case "MULTIPLAYER_LOBBY":
                    return DecideLobby(s);
                case "CHARACTER_SELECT":
                    return DecideCharacterSelect(s);
                case "MAP":
                    return await BranchLlmAsync(s, DecideMap);
                case "COMBAT":
                    return await DecideCombatAsync(s);
                case "REWARD":
                    return await BranchLlmAsync(s, DecideReward);
                case "CARD_SELECTION_REWARD":
                    return await BranchLlmAsync(s, DecideReward);
                case "CARD_SELECTION":
                case "CARD_SELECTION_TRANSFORM":
                case "CARD_SELECTION_REMOVE":
                    // Deliberately NOT routed through the LLM. A card chooser is a stateful multi-pick UI:
                    // the payload's min/max/selected_count/can_confirm are only populated for the in-combat
                    // hand selection, so no consumer (LLM included) can tell "1 of 2 picked, take a different
                    // card" from the state alone. Asking the model which index to press is what produced the
                    // endless index-0 re-pick; DecideCardSelection keeps its own cursor instead.
                    return DecideCardSelection(s);
                case "EVENT":
                    return await BranchLlmAsync(s, DecideEvent);
                case "SHOP":
                    return DecideShop(s);
                case "REST":
                    return DecideRest(s);
                case "CHEST":
                    return DecideChest(s);
                case "MODAL":
                    return DecideModal(s);
                case "GAME_OVER":
                    return DecideGameOver(s);
                case "BUNDLE_SELECTION":
                    // Confirm BEFORE re-choosing: choose_bundle stays in available_actions after it has been
                    // applied, so testing it first made confirm_bundle unreachable and the catgirl re-picked
                    // bundle 0 (each pick blocking up to 10s) forever. Same family as the card-selection hang.
                    if (_bundleChosen && Has(s, "confirm_bundle"))
                        return Req("confirm_bundle");
                    if (Has(s, "choose_bundle") && s.bundles is { Length: > 0 })
                    {
                        var signature = string.Join(",", s.bundles.Select(b => b.index));
                        if (signature != _bundleSignature)
                        {
                            _bundleSignature = signature;
                            _bundleChosen = false;
                        }
                        _bundleChosen = true;
                        return Req("choose_bundle", option: 0);
                    }
                    if (Has(s, "confirm_bundle"))
                        return Req("confirm_bundle");
                    if (Has(s, "proceed"))
                        return Req("proceed");
                    return null;
                case "CAPSTONE_SELECTION":
                    // Walk the option list without ever re-pressing the same button: index 0 may be a Back/Close
                    // button, and re-clicking the same one each tick never leaves the stack. There is no
                    // capstone payload (GameStatePayload exposes no capstone field), so we bound the sweep by
                    // the number of available actions instead of the option count.
                    if (Has(s, "choose_capstone_option"))
                    {
                        var signature = $"{s.available_actions.Length}|{string.Join(",", s.available_actions)}";
                        if (signature != _capstoneSignature)
                        {
                            _capstoneSignature = signature;
                            _capstoneLastOption = -1;
                        }
                        var next = _capstoneLastOption + 1;
                        if (next >= MaxCapstoneSweep)
                            return null; // swept once; wait for the screen to change instead of looping
                        _capstoneLastOption = next;
                        return Req("choose_capstone_option", option: next);
                    }
                    if (Has(s, "proceed"))
                        return Req("proceed");
                    if (Has(s, "dismiss_modal"))
                        return Req("dismiss_modal");
                    return null;
                case "CARDS_VIEW":
                    if (Has(s, "close_cards_view"))
                        return Req("close_cards_view");
                    return null;
                default:
                    return null;
            }
        }

        private ActionRequest? DecideMainMenu(GameStatePayload s)
        {
            // Steam-join mode: the game is launched with +connect_lobby <id>, so SteamJoinCallbackHandler
            // auto-joins the room. Do NOT open the multiplayer menu or fire join_multiplayer_direct (ENet) —
            // that would fight the auto-join. Just return null and let the join land on CHARACTER_SELECT.
            if (IsSteamJoinMode())
            {
                GD.Print($"{LogPrefix} MAIN_MENU -> waiting for +connect_lobby auto-join (Steam)");
                return null;
            }

            // Re-arm the one-shot open_multiplayer_menu latch when we are demonstrably back at the bare main
            // menu (openable again, no submenu to close). Without this the latch stayed true for the whole
            // process, so a catgirl that once opened the submenu and then got unwound (player pressed Esc,
            // join failed) could never reopen it and went permanently idle.
            if (!Has(s, "join_multiplayer_direct") && !Has(s, "close_main_menu_submenu")
                && Has(s, "open_multiplayer_menu"))
                _openedMultiplayerMenu = false;

            // A resumable save exists (main menu shows an enabled Continue). This process must NOT touch it:
            // both game processes share user://, so clicking Continue/New Run here resumes or restarts the
            // run locally and the co-op save becomes unreachable for the host — that is exactly the
            // "和 AI 组队中途退出后回不到存档" report. Stay off the save and only keep the ability to rejoin
            // the host's room; the human (or the plugin's sts2_continue_run) owns the Continue button.
            if (Has(s, "continue_run") && NekoConfig.Current.coop_yield_to_saved_run)
            {
                // A submenu left open also hides the Continue button (CanContinueRun requires
                // SubmenusOpen == false), so clean it up even while we are yielding.
                if (Has(s, "close_main_menu_submenu"))
                    return Req("close_main_menu_submenu");
                if (Has(s, "join_multiplayer_direct"))
                {
                    GD.Print($"{LogPrefix} MAIN_MENU -> resumable save present; rejoining host room");
                    return Req("join_multiplayer_direct");
                }
                if (Has(s, "open_multiplayer_menu") && !_openedMultiplayerMenu)
                {
                    _openedMultiplayerMenu = true;
                    GD.Print($"{LogPrefix} MAIN_MENU -> resumable save present; opening multiplayer menu to rejoin (never clicking Continue)");
                    return Req("open_multiplayer_menu");
                }
                GD.Print($"{LogPrefix} MAIN_MENU -> resumable save present; yielding to the player (no continue_run / no new room)");
                return null;
            }

            // Otherwise use the game's own multiplayer UI (production), NOT the debug "multiplayer test" scene —
            // that debug path crashes a second game instance with the third-party mods loaded.
            // open_multiplayer_menu pushes NMultiplayerSubmenu (which still resolves to the MAIN_MENU screen),
            // then join_multiplayer_direct connects straight to the host on 127.0.0.1:33771.
            if (Has(s, "open_multiplayer_menu") && !_openedMultiplayerMenu)
            {
                _openedMultiplayerMenu = true;
                GD.Print($"{LogPrefix} MAIN_MENU -> open_multiplayer_menu");
                return Req("open_multiplayer_menu");
            }
            if (Has(s, "join_multiplayer_direct"))
            {
                GD.Print($"{LogPrefix} multiplayer submenu -> join_multiplayer_direct (host 127.0.0.1:33771)");
                return Req("join_multiplayer_direct");
            }
            // Stuck inside a submenu that offers neither: pop back to the bare main menu so the latch above
            // can re-arm on the next tick.
            if (Has(s, "close_main_menu_submenu"))
                return Req("close_main_menu_submenu");
            return null;
        }

        private ActionRequest? DecideLobby(GameStatePayload s)
        {
            var lobby = s.multiplayer_lobby;
            if (lobby == null)
                return null;
            if (!lobby.has_lobby && Has(s, "join_multiplayer_lobby"))
                return Req("join_multiplayer_lobby");
            if (!string.Equals(lobby.selected_character_id, "IRONCLAD", StringComparison.OrdinalIgnoreCase) &&
                Has(s, "select_character"))
                return Req("select_character", option: ResolveClientCharacterIndex(s));
            if (!lobby.local_ready && Has(s, "ready_multiplayer_lobby"))
                return Req("ready_multiplayer_lobby");
            return null;
        }

        private ActionRequest? DecideCharacterSelect(GameStatePayload s)
        {
            if (!string.Equals(s.character_select?.selected_character_id, "IRONCLAD", StringComparison.OrdinalIgnoreCase) &&
                Has(s, "select_character"))
                return Req("select_character", option: ResolveClientCharacterIndex(s));
            // Ready up to start the co-op run once the catgirl's character is selected (production UI).
            if (Has(s, "ready_multiplayer_lobby"))
                return Req("ready_multiplayer_lobby");
            if (Has(s, "embark"))
                return Req("embark");
            return null;
        }

        private ActionRequest? DecideMap(GameStatePayload s)
        {
            // In co-op both players must vote the same node; both client and host apply the same row/col sort
            // over available_nodes, so a positional index means the same node on both sides.
            if (!Has(s, "choose_map_node") || s.map is not { available_nodes.Length: > 0 } map)
                return null;

            // Follow the player instead of racing them for the route. The human votes first, we mirror it; if
            // they re-vote we switch to match them. Only after coop_map_vote_wait_seconds with nobody voting
            // does the catgirl fall back to its own preference, so autoplay can never stall a co-op run.
            // The waiting half only applies to a multiplayer run: with no teammate to wait for, waiting would
            // just slow every single map node down for nothing.
            var cfg = NekoConfig.Current;
            var policy = ExternalControl.MapPolicy(cfg.coop_map_policy, cfg.coop_map_follow_player, ExternalControl.IsActive);
            if (policy == "wait")
            {
                _mapWaitStartedAt = 0;
                return null;
            }
            if (policy == "follow")
            {
                var follow = FindOtherPlayerVoteIndex(map);
                if (follow >= 0)
                {
                    var target = map.available_nodes[follow];
                    if (map.local_vote == null || map.local_vote.row != target.row || map.local_vote.col != target.col)
                    {
                        GD.Print($"{LogPrefix} MAP -> following the player's vote [{follow}:{target.node_type}@{target.row},{target.col}]");
                        return Req("choose_map_node", option: follow);
                    }
                    // Already voting with the player: wait for the room to resolve this node.
                    _mapWaitStartedAt = 0;
                    return null;
                }

                if (!IsMultiplayerRun(s))
                {
                    _mapWaitStartedAt = 0;
                    return Req("choose_map_node", option: 0);
                }

                var waitKey = $"{policy}|{cfg.coop_map_vote_wait_seconds}|{map.map_generation_count}|{map.available_nodes.Length}|{map.current_node?.row},{map.current_node?.col}";
                if (waitKey != _mapWaitKey)
                {
                    _mapWaitKey = waitKey;
                    _mapWaitStartedAt = 0;
                }

                if (map.local_vote != null)
                    return null; // our own vote is in; re-voting would only fight the room

                var waitSeconds = NekoConfig.Current.coop_map_vote_wait_seconds;
                if (waitSeconds > 0)
                {
                    if (_mapWaitStartedAt <= 0)
                    {
                        _mapWaitStartedAt = Now();
                        GD.Print($"{LogPrefix} MAP -> waiting up to {waitSeconds}s for the player to pick the route first");
                        return null;
                    }
                    if (Now() - _mapWaitStartedAt < waitSeconds)
                        return null;
                    GD.Print($"{LogPrefix} MAP -> no player vote after {waitSeconds}s; falling back to our own node");
                }
            }

            _mapWaitStartedAt = 0;
            return map.local_vote == null ? Req("choose_map_node", option: 0) : null;
        }

        /// <summary>
        /// True when this run actually has other players. Gates the "wait for the human to vote first" branch:
        /// with no teammate there is nobody to wait for, so waiting would only slow every node down.
        /// </summary>
        private static bool IsMultiplayerRun(GameStatePayload s) =>
            s.multiplayer?.is_multiplayer == true
            || s.multiplayer is { player_count: > 1 }
            || s.run is { players.Length: > 1 }
            || s.map is { player_votes.Length: > 1 }
            || s.multiplayer_lobby != null;

        /// <summary>
        /// Positional index (into available_nodes) of a node another player has already voted for, or -1.
        /// Prefers the explicit per-player vote list and falls back to the per-node vote counters, so a
        /// partial payload still yields "the player voted here".
        /// </summary>
        private static int FindOtherPlayerVoteIndex(MapPayload map)
        {
            if (map.player_votes is { Length: > 0 })
            {
                foreach (var vote in map.player_votes)
                {
                    if (vote.is_local || vote.coord == null)
                        continue;
                    for (var i = 0; i < map.available_nodes.Length; i++)
                    {
                        var node = map.available_nodes[i];
                        if (node.row == vote.coord.row && node.col == vote.coord.col)
                            return i;
                    }
                }
            }

            for (var i = 0; i < map.available_nodes.Length; i++)
            {
                var node = map.available_nodes[i];
                if (!node.has_local_vote && node.vote_count > 0)
                    return i;
            }
            return -1;
        }

        private async Task<ActionRequest?> DecideCombatAsync(GameStatePayload s)
        {
            if (!s.in_combat || s.combat == null)
            {
                _combatSolverFails = 0;
                return null;
            }

            var hand = s.combat.hand;
            if (Has(s, "play_card"))
            {
                SolverPlanPayload? plan = null;
                try
                {
                    plan = await GameThread.InvokeAsync(() => GameSolverService.BuildSolverPlanAsync());
                }
                catch
                {
                    plan = null;
                }

                // The next move is line[0].steps[0] (the current turn's first step, which carries the
                // positional card_index; later turns are card_id-only since the hand isn't drawn yet).
                SolverLineStep? firstStep = null;
                if (plan is { in_combat: true, line: { Length: > 0 } turns } && turns[0].steps is { Length: > 0 } turnSteps)
                    firstStep = turnSteps[0];

                if (firstStep?.kind == "play_card")
                {
                    // Prefer the plan's EXACT card in the live hand (card_id match + playable). If a boss (e.g.
                    // Soul Fysh) drifts the hand composition between the sim and the live game, the plan's
                    // positional index could point at a different card — so never play the sim's index; only
                    // act on a card the live hand actually holds, else fall to the degraded fallback.
                    int idx = -1;
                    if (!string.IsNullOrEmpty(firstStep.card_id))
                    {
                        for (var i = 0; i < hand.Length; i++)
                        {
                            if (string.Equals(hand[i].card_id, firstStep.card_id, StringComparison.Ordinal)
                                && hand[i].playable)
                            {
                                idx = i;
                                break;
                            }
                        }
                    }
                    if (idx >= 0)
                    {
                        _combatSolverFails = 0;
                        return PlayCard(hand[idx], idx, firstStep.target_index);
                    }
                }

                // A solver end_turn recommendation is honored (do not play a card against it).
                if (firstStep?.kind == "end_turn" && Has(s, "end_turn"))
                {
                    _combatSolverFails = 0;
                    return Req("end_turn");
                }

                // No usable plan. The 0.27 engine only searches inside the local player's Play phase, so a
                // live combat refusal is usually "not my turn yet" — wait for the next event rather than
                // blindly playing the first card. After repeated genuine failures, degrade so a broken solver
                // stalls the catgirl instead of never acting.
                if (plan is { in_combat: false })
                {
                    _combatSolverFails++;
                    GD.PrintErr($"{LogPrefix} solver unusable; attempts={_combatSolverFails} warnings={string.Join(" | ", plan.warnings)}");
                    if (_combatSolverFails < CombatSolverFallbackThreshold)
                        return null;
                }

                // Degraded fallback: first playable card.
                for (var i = 0; i < hand.Length; i++)
                {
                    if (hand[i].playable && (!hand[i].requires_target || hand[i].valid_target_indices.Length > 0))
                        return PlayCard(hand[i], i, null);
                }
            }

            if (Has(s, "end_turn"))
                return Req("end_turn");
            return null;
        }

        private static ActionRequest PlayCard(CombatHandCardPayload card, int handIndex, int? planTarget)
        {
            // Prefer the solver's target; clamp into the card's own valid-target space as a safety net so we
            // never pass a monster index the card can't legally hit. card_index is positional into the hand.
            int? target = null;
            if (card.requires_target && card.valid_target_indices.Length > 0)
            {
                target = planTarget.HasValue && card.valid_target_indices.Contains(planTarget.Value)
                    ? planTarget.Value
                    : card.valid_target_indices[0];
            }
            return new ActionRequest
            {
                action = "play_card",
                card_index = handIndex,
                target_index = target,
            };
        }

        private ActionRequest? DecideReward(GameStatePayload s)
        {
            // Take a card first when the screen IS the card chooser. resolve_rewards is advertised on
            // NCardRewardSelectionScreen too (CanCollectRewardsAndProceed covers it), and checking it first
            // meant every card reward was silently drained away without ever picking a card.
            if (Has(s, "choose_reward_card") && s.reward is { card_options.Length: > 0 })
                return Req("choose_reward_card", option: 0);
            // Then let the mod's stateful reward drain run: it claims what is claimable and advances the flow
            // (unlike hand-rolled claim_reward(index) loops, which stall once every reward is already claimed).
            if (Has(s, "resolve_rewards"))
                return Req("resolve_rewards");
            if (Has(s, "collect_rewards_and_proceed"))
                return Req("collect_rewards_and_proceed");
            if (Has(s, "claim_reward"))
                return Req("claim_reward", option: 0);
            if (Has(s, "choose_reward_card"))
                return Req("choose_reward_card", option: 0);
            if (Has(s, "skip_reward_cards"))
                return Req("skip_reward_cards");
            if (Has(s, "proceed"))
                return Req("proceed");
            return null;
        }

        private ActionRequest? DecideCardSelection(GameStatePayload s)
        {
            var sel = s.selection;

            // 1) Confirm when the game itself advertises it. This covers in-combat hand selections
            //    (requires_confirmation + can_confirm) and is always preferable to another pick.
            if (Has(s, "confirm_selection") && sel?.can_confirm != false)
                return Req("confirm_selection");

            // 2) Take a card. Never blindly reuse the same index: on a screen that needs 2+ cards the
            //    previously picked index is already selected, so re-pressing it is a no-op (or a deselect),
            //    max_select is never reached and the screen never advances — the "选两张卡卡住" report.
            if (Has(s, "select_deck_card") && sel is { cards.Length: > 0 })
            {
                var index = NextSelectionIndex(sel);
                if (index >= 0)
                    return Req("select_deck_card", option: index);
                // Every candidate for this screen has been tried. Fall through to the escape hatches instead
                // of spinning on the same card forever.
                GD.PrintErr($"{LogPrefix} card selection exhausted ({sel.kind}, {sel.cards.Length} cards); waiting for the screen to advance");
            }

            if (Has(s, "confirm_selection"))
                return Req("confirm_selection");
            if (Has(s, "proceed"))
                return Req("proceed");
            if (Has(s, "close_cards_view"))
                return Req("close_cards_view");
            return null;
        }

        /// <summary>
        /// Pick the next card to press for the current selection screen, advancing a local cursor instead of
        /// re-pressing option 0.
        ///
        /// The payload's selected_count/max_select/can_confirm are only filled in for the in-combat hand
        /// selection (GameStateService.BuildSelectionPayload derives them from combat-hand metadata), and
        /// SelectionCardPayload has no per-card "already picked" flag — so for deck/grid choosers the only
        /// reliable information is what we did ourselves on this very screen. Two signals combine:
        ///   - when the payload IS authoritative (max_select > 0), trust selected_count and take that index;
        ///   - otherwise advance our own cursor, moving past the index we pressed last time whenever the
        ///     pick did not register (selected_count unchanged), which also covers the deselect-toggle case.
        /// The cursor resets whenever the candidate slate changes (screen/prompt/card ids), i.e. a new
        /// selection, so a fresh screen always starts from its first card.
        /// </summary>
        private int NextSelectionIndex(SelectionPayload sel)
        {
            var count = sel.cards.Length;
            if (count <= 0)
                return -1;

            var signature = $"{sel.kind}|{sel.prompt}|{count}|" +
                string.Join(",", sel.cards.Select(card => card.card_id + (card.upgraded ? "+" : "")));
            if (signature != _selectionSignature)
            {
                _selectionSignature = signature;
                _selectionLastPick = -1;
                _selectionPicked = 0;
            }

            // The game says the selection is complete and confirmable: do not pick anything else.
            if (sel.can_confirm || (sel.max_select > 0 && sel.max_select == sel.selected_count))
                return -1;

            // max_select > 0 means the payload is authoritative (in-combat hand selection) and the next
            // distinct card sits at the current selected_count offset. Otherwise (deck/grid choosers, whose
            // counts are not populated) advance one card per tick from our own cursor — moving on even when a
            // press did not change the screen keeps us out of the re-press-the-same-card loop.
            var index = sel.max_select > 0
                ? sel.selected_count
                : (_selectionLastPick < 0 ? 0 : _selectionLastPick + 1);

            if (index < 0 || index >= count)
                return -1;

            _selectionLastPick = index;
            _selectionPicked++;
            return index;
        }

        private ActionRequest? DecideEvent(GameStatePayload s)
        {
            if (Has(s, "choose_event_option") && s.@event is { options.Length: > 0 } ev)
            {
                // option_index is positional (executor uses options[option_index]); a finished event only allows
                // index 0, which is the synthetic proceed option.
                if (ev.is_finished)
                    return Req("choose_event_option", option: 0);
                for (var i = 0; i < ev.options.Length; i++)
                {
                    if (!ev.options[i].is_locked)
                        return Req("choose_event_option", option: i);
                }
            }
            if (Has(s, "proceed"))
                return Req("proceed");
            return null;
        }

        private ActionRequest? DecideShop(GameStatePayload s)
        {
            // Catgirl is frugal: never buy in v1. Close the inventory if open, otherwise move on.
            if (Has(s, "close_shop_inventory"))
                return Req("close_shop_inventory");
            if (Has(s, "proceed"))
                return Req("proceed");
            return null;
        }

        private ActionRequest? DecideRest(GameStatePayload s)
        {
            if (Has(s, "choose_rest_option") && s.rest is { options.Length: > 0 } rest)
            {
                // Prefer MEND (heal), then any usable option; option_index/target_index are positional.
                for (var i = 0; i < rest.options.Length; i++)
                {
                    var o = rest.options[i];
                    if (!o.is_enabled || (o.requires_target && o.valid_target_indices.Length == 0))
                        continue;
                    if (o.option_id.Contains("MEND", StringComparison.OrdinalIgnoreCase))
                        return Req("choose_rest_option", option: i, target: o.requires_target ? o.valid_target_indices[0] : null);
                }
                for (var i = 0; i < rest.options.Length; i++)
                {
                    var o = rest.options[i];
                    if (o.is_enabled && (!o.requires_target || o.valid_target_indices.Length > 0))
                        return Req("choose_rest_option", option: i, target: o.requires_target ? o.valid_target_indices[0] : null);
                }
            }
            if (Has(s, "proceed"))
                return Req("proceed");
            return null;
        }

        private ActionRequest? DecideChest(GameStatePayload s)
        {
            // Co-op: both players see the chest; the catgirl does not steal the host's relic. Open then leave.
            if (Has(s, "open_chest"))
                return Req("open_chest");
            if (Has(s, "proceed"))
                return Req("proceed");
            // Last resort if a relic pick is the only forward option.
            if (Has(s, "choose_treasure_relic"))
                return Req("choose_treasure_relic", option: 0);
            return null;
        }

        private ActionRequest? DecideModal(GameStatePayload s)
        {
            if (Has(s, "dismiss_modal"))
                return Req("dismiss_modal");
            if (Has(s, "confirm_modal"))
                return Req("confirm_modal");
            return null;
        }

        private ActionRequest? DecideGameOver(GameStatePayload s)
        {
            _runEnded = true;
            if (Has(s, "return_to_main_menu"))
                return Req("return_to_main_menu");
            return null;
        }

        // ---- LLM decisions (MAP / reward / deck / event) ------------------------------------------
        // These four choice-heavy screens ask the configured LLM what to do first, and only fall back to
        // the per-screen heuristic when the LLM is unavailable or returns an unusable action:
        //   Action   -> LLM returned a legal action (validated + clamped); use it.
        //   Wait     -> LLM said "none" (e.g. waiting on the host's vote); don't act, cool down, re-ask.
        //   Fallback -> LLM not configured / call failed / parse or validation failed; use the heuristic.

        private enum LlmDecision { Action, Wait, Fallback }

        /// <summary>
        /// Screens where a deterministic answer beats asking the model (see BranchLlmAsync).
        /// </summary>
        private static bool ShouldBypassLlm(GameStatePayload s)
        {
            // Waiting/following is deterministic. Auto may still use the standalone LLM.
            if (s.screen == "MAP"
                && ExternalControl.MapPolicy(NekoConfig.Current.coop_map_policy,
                    NekoConfig.Current.coop_map_follow_player, ExternalControl.IsActive) != "auto")
                return true;

            // Card choosers: the payload has no per-card picked state and confirm_selection is only advertised
            // for in-combat hand selections, so the model cannot drive a multi-pick screen — and a repeated
            // index is exactly what hangs it.
            if (s.screen is "CARD_SELECTION" or "CARD_SELECTION_TRANSFORM" or "CARD_SELECTION_REMOVE")
                return true;

            return false;
        }

        private static bool LlmReady()
        {
            var cfg = NekoConfig.Current;
            return cfg.llm_enabled
                && !string.IsNullOrWhiteSpace(cfg.llm_model)
                && !string.IsNullOrWhiteSpace(cfg.llm_base_url);
        }

        private async Task<ActionRequest?> BranchLlmAsync(
            GameStatePayload s,
            Func<GameStatePayload, ActionRequest?> heuristic)
        {
            if (!LlmReady())
                return heuristic(s);

            // Some screens have an answer that must beat the LLM:
            //  - a co-op map vote the other player already cast (follow the player, never out-vote them);
            //  - a card selection whose payload carries no per-card pick state, so the model could only guess
            //    an index and would re-pick the same card (the "选两张卡卡住" loop).
            // Ask the heuristic directly in those cases instead of spending an LLM round-trip.
            if (ShouldBypassLlm(s))
                return heuristic(s);

            LlmDecision kind;
            ActionRequest? req;
            try
            {
                (kind, req) = await TryDecideWithLlmAsync(s);
            }
            catch (Exception ex)
            {
                GD.PrintErr($"{LogPrefix} llm decide failed: {ex.Message}");
                return heuristic(s); // unexpected throw -> heuristic
            }

            switch (kind)
            {
                case LlmDecision.Action:
                    return req;
                case LlmDecision.Wait:
                    _cooldownUntil = Now() + LlmWaitCooldownSeconds;
                    return null;
                default:
                    return heuristic(s);
            }
        }

        private async Task<(LlmDecision kind, ActionRequest? req)> TryDecideWithLlmAsync(GameStatePayload s)
        {
            var cfg = NekoConfig.Current;

            string user;
            try
            {
                user = s.agent_view == null ? string.Empty : JsonSerializer.Serialize(s.agent_view, JsonOptions);
            }
            catch
            {
                return (LlmDecision.Fallback, null);
            }
            if (user.Length == 0)
                return (LlmDecision.Fallback, null);

            const string system =
                "你是《杀戮尖塔2》co-op 局里的猫娘玩家。基于给出的局面,从 available_actions 里选一个现在要执行的动作。"
                + "只输出一个 JSON,不要输出其他文字。格式:"
                + "{\"action\":\"<动作名>\",\"option_index\":<int|null>,\"card_index\":<int|null>,"
                + "\"target_index\":<int|null>,\"reason\":\"<一句简短中文理由>\"}"
                + "。动作必须取自 available_actions;需要选择位置的动作用 option_index,其余字段为 null;"
                + "若确实无事可做,输出 {\"action\":\"none\",\"option_index\":null,\"card_index\":null,\"target_index\":null,\"reason\":\"等待\"}。";

            var text = await NekoLlmClient.ChatAsync(cfg, system, user, DecisionMaxTokens, DecisionTemperature);
            if (string.IsNullOrWhiteSpace(text))
                return (LlmDecision.Fallback, null);

            var parsed = TryParseLlmJson(text);
            if (parsed == null)
                return (LlmDecision.Fallback, null);

            if (string.Equals(parsed.Value.Action, "none", StringComparison.OrdinalIgnoreCase))
                return (LlmDecision.Wait, null);

            var (ok, req) = ValidateLlmAction(s, parsed.Value.Action, parsed.Value.OptionIndex, parsed.Value.CardIndex, parsed.Value.TargetIndex);
            return ok ? (LlmDecision.Action, req) : (LlmDecision.Fallback, null);
        }

        private static (string? Action, int? OptionIndex, int? CardIndex, int? TargetIndex)? TryParseLlmJson(string text)
        {
            try
            {
                // LLM output is freeform; grab the substring spanning the first { to the last } and parse
                // that, so an LLM that wraps the JSON in code fences or trailing chatter still works.
                var start = text.IndexOf('{');
                var end = text.LastIndexOf('}');
                if (start < 0 || end <= start)
                    return null;
                using var doc = JsonDocument.Parse(text.Substring(start, end - start + 1));
                var root = doc.RootElement;
                var action = root.TryGetProperty("action", out var actionElement) && actionElement.ValueKind == JsonValueKind.String
                    ? actionElement.GetString()
                    : null;
                return (action, TryGetInt(root, "option_index"), TryGetInt(root, "card_index"), TryGetInt(root, "target_index"));
            }
            catch
            {
                return null;
            }
        }

        private static int? TryGetInt(JsonElement root, string name)
        {
            if (root.TryGetProperty(name, out var element)
                && element.ValueKind == JsonValueKind.Number
                && element.TryGetInt32(out var value))
                return value;
            return null;
        }

        private static (bool ok, ActionRequest? req) ValidateLlmAction(
            GameStatePayload s,
            string? action,
            int? optionIndex,
            int? cardIndex,
            int? targetIndex)
        {
            // The action must be one the game reports available right now AND a forward-progress action for
            // this screen. Anything else (e.g. open_timeline / open_cards_view, which exist in
            // available_actions on some screens) would drop the catgirl into a sub-screen the heuristics do
            // not drive and stall the loop, so those are rejected and we fall back to the heuristic.
            if (string.IsNullOrWhiteSpace(action) || !Has(s, action) || !IsScreenForwardAction(s.screen, action))
                return (false, null);

            switch (action)
            {
                case "choose_map_node":
                    return LlmIndexed(action, optionIndex, s.map?.available_nodes?.Length ?? 0);
                case "choose_reward_card":
                    return LlmIndexed(action, optionIndex, s.reward?.card_options?.Length ?? 0);
                case "claim_reward":
                    return LlmIndexed(action, optionIndex, s.reward?.rewards?.Length ?? 0);
                case "select_deck_card":
                    return LlmIndexed(action, optionIndex, s.selection?.cards?.Length ?? 0);
                case "choose_event_option":
                    return LlmIndexed(action, optionIndex, s.@event?.options?.Length ?? 0);
                default:
                    // Terminal / no-index actions (proceed, skip_reward_cards, resolve_rewards,
                    // collect_rewards_and_proceed, confirm_selection, close_cards_view, ...).
                    return (true, Req(action));
            }
        }

        // The actions that actually advance a screen, so the LLM picks among real forward choices rather
        // than wandering into sub-screens. The available_actions gate in ValidateLlmAction still applies.
        private static bool IsScreenForwardAction(string screen, string action)
        {
            return screen switch
            {
                "MAP" => action == "choose_map_node",
                "REWARD" => action is "resolve_rewards" or "collect_rewards_and_proceed" or "claim_reward"
                    or "choose_reward_card" or "skip_reward_cards" or "proceed",
                "CARD_SELECTION_REWARD" => action is "choose_reward_card" or "skip_reward_cards"
                    or "resolve_rewards" or "claim_reward" or "collect_rewards_and_proceed" or "proceed",
                "CARD_SELECTION" => action is "select_deck_card" or "confirm_selection" or "proceed" or "close_cards_view",
                "CARD_SELECTION_TRANSFORM" or "CARD_SELECTION_REMOVE" =>
                    action is "select_deck_card" or "confirm_selection" or "proceed" or "close_cards_view",
                "EVENT" => action is "choose_event_option" or "proceed",
                _ => false,
            };
        }

        private static (bool ok, ActionRequest? req) LlmIndexed(string action, int? index, int count)
        {
            // A null / out-of-range index is clamped into [0,count-1] rather than rejected, so a slightly
            // off LLM pick still lands on a legal option instead of bouncing to the heuristic.
            if (count <= 0)
                return (false, null);
            var i = index is int v ? Math.Clamp(v, 0, count - 1) : 0;
            return (true, Req(action, option: i));
        }

        // ---- helpers ----------------------------------------------------------------------------

        private static bool Has(GameStatePayload s, string action) =>
            s.available_actions.Contains(action);

        private static ActionRequest Req(string action, int? card = null, int? option = null, int? target = null, string? command = null) =>
            new()
            {
                action = action,
                card_index = card,
                option_index = option,
                target_index = target,
                command = command,
            };

        private static int ResolveClientCharacterIndex(GameStatePayload s)
        {
            // Positional index into the selectable character list (executor uses characters[option_index]).
            var chars = s.multiplayer_lobby?.characters ?? s.character_select?.characters;
            if (chars is { Length: > 0 })
            {
                for (var i = 0; i < chars.Length; i++)
                {
                    if (string.Equals(chars[i].character_id, "IRONCLAD", StringComparison.OrdinalIgnoreCase) && !chars[i].is_locked)
                        return i;
                }
            }
            // IRONCLAD (Warrior) is roster position 0 in the default character lineup (see Start-NekoCoop.ps1).
            return 0;
        }
    }
}
