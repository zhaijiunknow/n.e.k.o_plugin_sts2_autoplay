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

        private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = false };

        private readonly object _gate = new();
        private bool _started;
        private bool _inFlight;
        private bool _openedMultiplayerMenu;
        private bool _runEnded;
        private double _lastActionAt;
        private double _cooldownUntil;
        private int _combatSolverFails;

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
            if (_inFlight)
                return;

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
            // Runtime guard: if a lobby/run already tells us we're the host, never self-drive (mis-wired setup).
            if (state.multiplayer_lobby?.is_host == true ||
                string.Equals(state.multiplayer?.net_game_type, "host", StringComparison.OrdinalIgnoreCase))
            {
                GD.PrintErr($"{LogPrefix} port says catgirl client but runtime says host; disabling autoplay");
                return;
            }

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

            _inFlight = true;
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
                case "CARD_SELECTION":
                    return await BranchLlmAsync(s, DecideCardSelection);
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
                    if (Has(s, "choose_bundle") && s.bundles is { Length: > 0 })
                        return Req("choose_bundle", option: 0);
                    if (Has(s, "confirm_bundle"))
                        return Req("confirm_bundle");
                    return null;
                case "CAPSTONE_SELECTION":
                    if (Has(s, "choose_capstone_option"))
                        return Req("choose_capstone_option", option: 0);
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
            return null;
        }

        private ActionRequest? DecideLobby(GameStatePayload s)
        {
            var lobby = s.multiplayer_lobby;
            if (lobby == null)
                return null;
            if (!lobby.has_lobby && Has(s, "join_multiplayer_lobby"))
                return Req("join_multiplayer_lobby");
            if (!string.Equals(lobby.selected_character_id, "DEFECT", StringComparison.OrdinalIgnoreCase) &&
                Has(s, "select_character"))
                return Req("select_character", option: ResolveClientCharacterIndex(s));
            if (!lobby.local_ready && Has(s, "ready_multiplayer_lobby"))
                return Req("ready_multiplayer_lobby");
            return null;
        }

        private ActionRequest? DecideCharacterSelect(GameStatePayload s)
        {
            if (!string.Equals(s.character_select?.selected_character_id, "DEFECT", StringComparison.OrdinalIgnoreCase) &&
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
            // over available_nodes, so positional index 0 is the same node on both sides.
            if (Has(s, "choose_map_node") && s.map is { available_nodes.Length: > 0 })
                return Req("choose_map_node", option: 0);
            return null;
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

                if (firstStep is { kind: "play_card", card_index: int idx }
                    && idx >= 0 && idx < hand.Length && hand[idx].playable)
                {
                    _combatSolverFails = 0;
                    return PlayCard(hand[idx], idx, firstStep.target_index);
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
            if (Has(s, "select_deck_card"))
                return Req("select_deck_card", option: 0);
            if (Has(s, "confirm_selection"))
                return Req("confirm_selection");
            if (Has(s, "proceed"))
                return Req("proceed");
            if (Has(s, "close_cards_view"))
                return Req("close_cards_view");
            return null;
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
                "CARD_SELECTION" => action is "select_deck_card" or "confirm_selection" or "proceed" or "close_cards_view",
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
                    if (string.Equals(chars[i].character_id, "DEFECT", StringComparison.OrdinalIgnoreCase) && !chars[i].is_locked)
                        return i;
                }
            }
            return 4; // DEFECT is roster position 4 in the default character lineup (see Start-NekoCoop.ps1).
        }
    }
}
