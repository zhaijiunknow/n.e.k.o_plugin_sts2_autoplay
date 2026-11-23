// 战斗求解器门面(Phase 5)。
// /solver/plan 现在走 vendored CombatSolver 搜索脑(CombatSolverFacade -> CombatRootSnapshot.Capture
// -> HotSearchCoordinator.Solve)求出忠实的最优路线,再映射回本文件定义的 SolverPlanPayload。
// 旧的“轻量 int 近似 + beam 手工评分”实现(SimSolverPlan)保留为回退(SolverEnabled=false 时的
// 后备路径)。多人(co-op)战斗同样走 CombatSolver:内核只为本地玩家规划,人数带来的敌方缩放冻结在根里,
// 队友不建模(计划的 warnings 里会注明)。本文件只保留 payload 类型契约与入口,确保 Router / 插件契约不变。
using System;
using System.Threading;
using System.Threading.Tasks;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Runs;
using NekoComm.Game.Sim;

namespace NekoComm.Game
{
    internal static class GameSolverService
    {
        // The vendored CombatSolver search brain is the authoritative solver for /solver/plan.
        // Flip to false to fall back to the self-built SimSolverPlan path.
        public static bool SolverEnabled = true;

        // ★★★ t46：`/solver/plan` 的**时间预算**必须由本模组收口，不能用上游默认值。
        //
        // ## 为什么（玩家口径 + 三份实机日志）
        //   玩家：「让插件等待求解器，不需要超时设置，不然超时之后会退级策略会影响求解器的结果，
        //          可能出现本来能过的过不了了。」⇒ 插件侧已经把 `/solver/plan` 改成**无读超时**
        //   （`transport_client.get_combat_plan`），它现在会老老实实等到我们返回。
        //   但"没有上限"不等于"合理"：0.50.1 时代上游默认 Deep 120 s + 120 s 补充审计，实测单次
        //   请求 **78 s / 137 s（均值）/ 155 s，最长 329 s**（`Desktop\新建文件夹` 三份 godot.log）。
        //   一回合动作等两分钟，玩家就是在看静止画面 ⇒ 这个尾部必须有上限。
        //
        // ## 为什么是"压在 mod 侧"而不是"让插件超时"
        //   超时放弃 = 换策略 = 结果被悄悄改掉（玩家明确禁止）。所以只能由产生耗时的一侧
        //   主动把预算压到可玩范围：引擎在时间边界按上游既有语义**发布已算出的前沿**
        //   （`search_status=budget_exceeded`，见 CombatSolverFacade.MapStatus），
        //   而不是返回"没有计划" ⇒ 准入门槛照样通过，动作照常出。
        //
        // ## 取值（CombatSolver 1.0.0 的自适应三段式搜索）
        //   引擎先跑 Stage 1（束宽 96 / 15 万节点，普通回合到此为止）；只有"难打"的局面才继续
        //   Stage 2/3 加深，加深的墙钟上限就是 `SearchPolicySnapshot.MaxHardFightThinkSeconds`
        //   （上游默认 4 s，引擎自己钳到 1..15 s）。`NekoConfig.solver_budget_seconds`（默认 12 s）
        //   映射到这个上限 ⇒ 语义不变：它是"一次请求最多想多久"，不是"每次都想这么久"。

        /// <summary>按 `NekoConfig.solver_budget_seconds` 生成 facade 选项（难局加深的墙钟上限）。</summary>
        internal static SolverFacadeOptions PlanOptions()
        {
            SolverFacadeOptions defaults = SolverFacadeOptions.Default;
            double budgetSeconds = NekoConfig.Current.solver_budget_seconds;
            // 0/负数/非有限值 ⇒ 显式不覆盖，保持上游默认（4 s）。
            if (double.IsNaN(budgetSeconds) || double.IsInfinity(budgetSeconds) || budgetSeconds <= 0)
                return defaults;
            return new SolverFacadeOptions
            {
                // 除思考上限外一律沿用上游默认（并行度等），保证"策略没变、只是搜索时间有上限"。
                MaxThinkSeconds = budgetSeconds,
                MaxDegreeOfParallelism = defaults.MaxDegreeOfParallelism,
            };
        }

        public static async Task<SolverPlanPayload> BuildSolverPlanAsync(CancellationToken cancellationToken = default)
        {
            var combat = CombatManager.Instance.DebugOnlyGetState();
            var run = RunManager.Instance.DebugOnlyGetState();
            if (combat == null || !CombatManager.Instance.IsInProgress)
                return new SolverPlanPayload { in_combat = false };

            var me = GameStateService.GetLocalPlayer(combat);
            if (me == null || me.PlayerCombatState == null)
                return new SolverPlanPayload { in_combat = false };

            if (SolverEnabled)
            {
                try
                {
                    // ★ t46：显式传预算（见 PlanOptions 的长注释）。
                    return await CombatSolverFacade.BuildPlanAsync(combat, me, PlanOptions(), cancellationToken);
                }
                catch
                {
                    return new SolverPlanPayload { in_combat = false };
                }
            }

            try
            {
                var budget = new SimBudget(250, 4000, 3000);
                return SimSolverPlan.Build(combat, me, budget);
            }
            catch
            {
                return new SolverPlanPayload { in_combat = false };
            }
        }
    }

    internal sealed class SolverPlanPayload
    {
        public bool in_combat { get; init; }
        public int? turn { get; init; }
        public double? score { get; init; }
        // Fingerprint of the combat state this plan was solved from (hash of the capture's ContinuationStamp
        // state text). Consumers compare it against the current state to decide whether the plan needs
        // recomputing after an action. Null when a plan was not produced from a live capture (e.g. fallback).
        public string? state_fingerprint { get; init; }

        // The forecasted line: grouped per turn, each turn's steps ending with an "end_turn" boundary.
        // The single source of truth for the next move is line[0].steps[0] (the current turn's first step,
        // which carries the positional card_index); there is no duplicated top-level action/card_index/etc.
        public SolverTurnStep[] line { get; init; } = Array.Empty<SolverTurnStep>();
        public int beam_width { get; init; }
        public int horizon { get; init; }
        public int max_turn_actions { get; init; }
        public string draw_model { get; init; } = "none";
        public string[] warnings { get; init; } = Array.Empty<string>();
        public CoverageSummaryPayload coverage { get; init; } = new();
        // Win-probability search fields (Phase 4: replaces the linear score with P(victory)).
        public double? win_prob { get; init; }
        public string? search_status { get; init; }   // complete | budget_exceeded
        public long? budget_ms { get; init; }
        public int? nodes_expanded { get; init; }
        public int? rollouts_total { get; init; }
        public double? confidence { get; init; }
        public string? policy_explanation { get; init; }
    }

    internal sealed class SolverLineStep
    {
        public string kind { get; init; } = "play_card";
        public int? card_index { get; init; }
        public string? card_id { get; init; }
        // Human-readable name for the card (or potion) — the engine's id (card_id) is the model key; the
        // name is what the LLM / UI should surface.
        public string? card_name { get; init; }
        public int? target_index { get; init; }
        // 任意计划的卡牌选择（消耗 / 拉弃牌堆 / 生成到手 / 变换 / 弃牌 等）：solver 在这步选定的那张牌。
        // 插件在随后的选牌界面（combat_hand_select / card_select）里按它选。无选择为 null。
        public string? choice_card_id { get; init; }
        public string? choice_card_name { get; init; }
    }

    /// <summary>
    /// One turn's worth of the forecasted line, grouped so consumers can render/act per-turn. Each turn's
    /// <see cref="steps"/> ends with an "end_turn" step (the end-turn boundary); only the first (current)
    /// turn's card_index is a positional hand index — later turns are card_id-only (hand unknown yet).
    /// </summary>
    internal sealed class SolverTurnStep
    {
        public int turn { get; init; }
        public SolverLineStep[] steps { get; init; } = Array.Empty<SolverLineStep>();
    }

    internal sealed class CoverageSummaryPayload
    {
        public int exact { get; init; }
        public int inferred { get; init; }
        public int unsupported { get; init; }
        public int ignored { get; init; }
        public int potions { get; init; }
        public bool risk { get; init; }
    }
}
