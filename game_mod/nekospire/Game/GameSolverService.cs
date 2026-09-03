// 战斗求解器门面(Phase 5)。
// /solver/plan 现在走 vendored CombatSolver 搜索脑(CombatSolverFacade -> CombatRootSnapshot.Capture
// -> CombatSearchCoordinator.Solve)求出忠实的最优路线,再映射回本文件定义的 SolverPlanPayload。
// 旧的“轻量 int 近似 + beam 手工评分”实现(SimSolverPlan)保留为回退(SolverEnabled=false 时的
// 后备路径),但默认已切换到 CombatSolver 脑。本文件只保留 payload 类型契约与入口,确保 Router /
// 插件契约不变。
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
                    return await CombatSolverFacade.BuildPlanAsync(combat, me, null, cancellationToken);
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
        // 当这步是打出"要求从手牌选一张消耗"的卡（如升级版坚毅/TrueGrit）时，solver 选定的那张牌；
        // 插件在随之弹出的 combat_hand_select 里按它选。普通出牌为 null。
        public string? exhaust_card_id { get; init; }
        public string? exhaust_card_name { get; init; }
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
