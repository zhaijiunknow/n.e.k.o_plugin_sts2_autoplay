// Standalone catgirl-danmaku driver for NekoSpire. Subscribes to the mod's GameEventService snapshot-diff
// events; on interesting scene/phase changes it calls the user-configured OpenAI-compatible LLM API (via
// HttpClient, off the game thread) to produce a short catgirl line, then renders it through the existing
// DanmakuService overlay. Silent on no-config / failure / throttle, so the standalone build stays quiet
// until the user enables + configures their LLM.
using System;
using System.Diagnostics;
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
            return type is "combat_started" or "combat_ended" or "screen_changed" or
                "reward_decision_required" or "event_state_changed" or "available_actions_changed";
        }

        private static async Task<string?> CallLlmAsync(NekoConfig cfg)
        {
            var situation = BuildSituation();
            var hint = await SolverHintAsync();
            var user = string.IsNullOrEmpty(hint) ? situation : situation + "。求解器建议:" + hint;
            const string system = "你是《杀戮尖塔2》的陪玩猫娘。用一句短小、猫娘口吻的话(不超过10字)点评当前局面,"
                + "并自然地带出求解器建议(如果给了),带对应情绪(战斗紧张/奖励期待/商店心动/事件好奇/火堆放松),"
                + "句末带你的口癖。只输出这一句。";
            return await NekoLlmClient.ChatAsync(cfg, system, user, cfg.llm_max_tokens, temperature: 0.9);
        }

        private static string BuildSituation()
        {
            try
            {
                var state = GameStateService.BuildStatePayload();
                var b = new StringBuilder("场景:").Append(state.screen);
                if (state.in_combat)
                {
                    b.Append(";回合:").Append(state.turn?.ToString() ?? "?");
                }
                b.Append(";Run:").Append(state.run_id);
                return b.ToString();
            }
            catch
            {
                return "当前局面未知。";
            }
        }

        /// <summary>Pull the /solver/plan recommendation into a short one-line hint (in-combat only).</summary>
        private static async Task<string?> SolverHintAsync()
        {
            try
            {
                var plan = await GameSolverService.BuildSolverPlanAsync();
                if (!plan.in_combat || string.IsNullOrEmpty(plan.action) || plan.action == "none")
                    return null;
                var b = new StringBuilder();
                b.Append("建议:").Append(plan.action);
                if (plan.card_index.HasValue)
                    b.Append(" 手牌").Append(plan.card_index.Value + 1);
                if (!string.IsNullOrEmpty(plan.card_id))
                    b.Append("(").Append(plan.card_id).Append(")");
                if (plan.target_index.HasValue)
                    b.Append(" 目标").Append(plan.target_index.Value + 1);
                if (plan.win_prob.HasValue)
                    b.Append(" 胜率").Append(plan.win_prob.Value.ToString("P0"));
                return b.ToString();
            }
            catch
            {
                return null;
            }
        }
    }
}
