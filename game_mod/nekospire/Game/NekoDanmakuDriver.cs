// Standalone catgirl-danmaku driver for NekoSpire. Subscribes to the mod's GameEventService snapshot-diff
// events; on interesting scene/phase changes it calls the user-configured OpenAI-compatible LLM API (via
// HttpClient, off the game thread) to produce a short catgirl line, then renders it through the existing
// DanmakuService overlay. Silent on no-config / failure / throttle, so the standalone build stays quiet
// until the user enables + configures their LLM.
using System;
using System.Diagnostics;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using NekoComm.Server;

namespace NekoComm.Game
{
    internal sealed class NekoDanmakuDriver
    {
        private static readonly Lazy<NekoDanmakuDriver> Lazy = new();
        public static NekoDanmakuDriver Instance => Lazy.Value;

        private const double MinIntervalSeconds = 8.0;

        private readonly object _gate = new();
        private bool _started;
        private double _lastFireAt;
        private string _lastEventKey = "";

        public void Start()
        {
            lock (_gate)
            {
                if (_started)
                    return;
                // Danmaku (catgirl commentary) is decoupled from the autoplay decision-LLM (llm_enabled).
                // The catgirl (co-op autoplay client) process runs headless and must not generate
                // commentary, so it is suppressed here regardless of llm_enabled. danmaku_enabled is the
                // explicit global toggle.
                if (!NekoConfig.Current.danmaku_enabled || NekoAutoplayDriver.IsCatgirlProcess())
                {
                    _started = true;   // marked started so the event loop never spins up
                    return;
                }
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
                    GameEventEnvelope ev;
                    try
                    {
                        ev = await subscription.Reader.ReadAsync();
                    }
                    catch
                    {
                        break;
                    }
                    await OnEventAsync(ev.type ?? "");
                }
            }
            catch
            {
                // Subscription/read failure: stop quietly; restart is not required for the standalone build.
            }
        }

        private async Task OnEventAsync(string type)
        {
            if (!IsInteresting(type))
                return;
            var cfg = NekoConfig.Current;
            if (!cfg.llm_enabled || string.IsNullOrWhiteSpace(cfg.llm_model) || string.IsNullOrWhiteSpace(cfg.llm_base_url))
                return;

            lock (_gate)
            {
                var now = (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;
                if (now - _lastFireAt < MinIntervalSeconds)
                    return;
                _lastFireAt = now;
            }

            var key = type + "|" + cfg.llm_model + "|" + cfg.llm_base_url;
            if (key == _lastEventKey)
                return; // same config + same broadcast -> skip (dedup)
            _lastEventKey = key;

            try
            {
                var text = await CallLlmAsync(cfg);
                if (string.IsNullOrWhiteSpace(text))
                    return;
                await GameThread.InvokeAsync(() => DanmakuService.PushAsync(text, "catgirl", "scrolling", null));
            }
            catch
            {
                // LLM failure/timeout/parse -> silent.
            }
        }

        private static bool IsInteresting(string type)
        {
            // Combat: comment on turn boundaries (combat_turn_changed + player_action_window_opened) so the
            // catgirl gives one line per turn based on the current plan, plus the outer lifecycle events.
            return type is "combat_started" or "combat_ended" or "combat_turn_changed" or "player_action_window_opened"
                or "screen_changed" or "reward_decision_required" or "event_state_changed" or "available_actions_changed";
        }

        private static async Task<string?> CallLlmAsync(NekoConfig cfg)
        {
            var situation = BuildSituation();
            var hint = await SolverHintAsync();
            var user = string.IsNullOrEmpty(hint) ? situation : situation + "。求解器建议:" + hint;
            const string system = "你是《杀戮尖塔2》的陪玩猫娘。用一句短小、猫娘口吻的话点评当前局面(不超过20字),"
                + "并给出一条具体建议(地图:走哪个节点/奖励或选牌:选哪张、跳不跳/事件:选哪个/战斗:怎么打),"
                + "自然带出求解器建议(如果给了),带对应情绪(战斗紧张/奖励期待/商店心动/事件好奇/火堆放松),"
                + "句末带你的口癖。只输出这一句。";
            return await NekoLlmClient.ChatAsync(cfg, system, user, cfg.llm_max_tokens, temperature: 0.9);
        }

        // Build a per-screen situation so the catgirl gives real advice: which map node, which reward /
        // deck card to take, which event option, and (in combat) the plan + HP/energy/hand/enemies.
        private static string BuildSituation()
        {
            try
            {
                var state = GameStateService.BuildStatePayload();
                var b = new StringBuilder();
                b.Append("场景:").Append(state.screen);
                if (state.in_combat)
                    b.Append(";回合:").Append(state.turn?.ToString() ?? "?");
                b.Append(";Run:").Append(state.run_id);

                switch (state.screen)
                {
                    case "MAP":
                        var nodes = state.map?.available_nodes;
                        if (nodes is { Length: > 0 })
                        {
                            b.Append(";地图可选节点:");
                            foreach (var n in nodes)
                                b.Append($"[{n.index}:{n.node_type}@{n.row},{n.col}" +
                                    (n.has_local_vote ? "已投" : n.vote_count > 0 ? $"票{n.vote_count}" : "") + "]");
                        }
                        break;
                    case "REWARD":
                    case "CARD_SELECTION_REWARD":
                        if (state.reward?.card_options is { Length: > 0 } rcards)
                        {
                            b.Append(";奖励卡可选:");
                            foreach (var c in rcards)
                                b.Append($"[{c.index}:{c.name}({c.energy_cost}费){ShortRule(c.resolved_rules_text)}]");
                            if (state.reward.alternatives.Length > 0)
                                b.Append(";可跳过");
                        }
                        break;
                    case "CARD_SELECTION":
                    case "CARD_SELECTION_TRANSFORM":
                    case "CARD_SELECTION_REMOVE":
                        if (state.selection?.cards is { Length: > 0 } scards)
                        {
                            b.Append(";可选卡:");
                            foreach (var c in scards)
                                b.Append($"[{c.index}:{c.name}({c.energy_cost}费){ShortRule(c.resolved_rules_text)}]");
                        }
                        break;
                    case "EVENT":
                        if (state.@event?.options is { Length: > 0 } evs)
                        {
                            b.Append(";事件选项:");
                            foreach (var o in evs)
                                b.Append($"[{o.index}:{(o.is_locked ? "锁定:" : "")}{o.title}]");
                        }
                        break;
                    case "SHOP":
                        b.Append(";商店可买:卡").Append(state.shop?.cards?.Length ?? 0)
                            .Append(" 遗物").Append(state.shop?.relics?.Length ?? 0)
                            .Append(" 药水").Append(state.shop?.potions?.Length ?? 0)
                            .Append(" 金币").Append(state.run?.gold ?? 0);
                        break;
                    case "REST":
                        if (state.rest?.options is { Length: > 0 } rests)
                            b.Append(";火堆可选:").Append(string.Join(",", rests.Select(o => o.is_enabled ? o.title : $"({o.title})")));
                        break;
                    case "COMBAT":
                        if (state.combat is { } cb)
                        {
                            b.Append($";我:{cb.player.current_hp}/{cb.player.max_hp} 能量{cb.player.energy} 格挡{cb.player.block} 星{cb.player.stars}");
                            if (cb.hand is { Length: > 0 })
                                b.Append(";手牌:").Append(string.Join(",", cb.hand.Select(h => h.name + (h.playable ? "" : "(不可打)"))));
                            if (cb.enemies is { Length: > 0 })
                                b.Append(";敌:").Append(string.Join(",", cb.enemies.Select(e => $"{e.name}HP{e.current_hp}/{e.max_hp}")));
                        }
                        break;
                }
                return b.ToString();
            }
            catch
            {
                return "当前局面未知。";
            }
        }

        private static string ShortRule(string text)
        {
            var t = string.IsNullOrWhiteSpace(text) ? "" : text.Trim();
            return t.Length <= 40 ? t : t[..40] + "…";
        }

        /// <summary>Pull the /solver/plan recommendation into a short one-line hint (in-combat only).</summary>
        // Feed the current turn's whole planned sequence (line[0].steps, ending with end_turn) so the LLM
        // sees the full "how to play this turn" plan — not just the first move — and can turn it into a
        // spoken guideline (先打X再补Y最后结束).
        private static async Task<string?> SolverHintAsync()
        {
            try
            {
                var plan = await GameSolverService.BuildSolverPlanAsync();
                if (!plan.in_combat)
                    return null;
                if (plan.line is not { Length: > 0 } turns || turns[0].steps is not { Length: > 0 } steps)
                    return null;
                var b = new StringBuilder("本轮建议:");
                foreach (var s in steps)
                {
                    switch (s.kind)
                    {
                        case "play_card":
                            b.Append(s.card_name?.Length > 0 ? $"打{s.card_name}" : s.card_id?.Length > 0 ? $"打{s.card_id}" : "打牌");
                            if (s.card_index.HasValue)
                                b.Append($"[手{s.card_index.Value + 1}]");
                            if (s.target_index.HasValue)
                                b.Append($"→{s.target_index.Value + 1}");
                            break;
                        case "use_potion":
                            b.Append($"用药水{(s.card_name?.Length > 0 ? s.card_name : s.card_id)}");
                            break;
                        case "end_turn":
                            b.Append("→结束");
                            break;
                    }
                    b.Append(";");
                }
                if (plan.win_prob.HasValue)
                    b.Append($" 胜率{plan.win_prob.Value:P0}");
                return b.ToString();
            }
            catch
            {
                return null;
            }
        }
    }
}
