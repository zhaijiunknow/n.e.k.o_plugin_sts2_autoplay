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
                return new SolverPlanPayload { in_combat = false, reason = "not_in_combat" };

            var me = GameStateService.GetLocalPlayer(combat);
            if (me == null || me.PlayerCombatState == null)
                return new SolverPlanPayload { in_combat = false, reason = "no_local_player" };

            if (SolverEnabled)
            {
                try
                {
                    return await CombatSolverFacade.BuildPlanAsync(combat, me, null, cancellationToken);
                }
                catch
                {
                    return new SolverPlanPayload { in_combat = false, reason = "solver_failed" };
                }
            }

            try
            {
                var budget = new SimBudget(250, 4000, 3000);
                return SimSolverPlan.Build(combat, me, budget);
            }
            catch
            {
                return new SolverPlanPayload { in_combat = false, reason = "solver_failed" };
            }
        }
    }

    internal sealed class SolverPlanPayload
    {
        public bool in_combat { get; init; }
        public int? turn { get; init; }
        public double? score { get; init; }
        public string action { get; init; } = "none";
        public int? card_index { get; init; }
        public string? card_id { get; init; }
        public int? target_index { get; init; }
        public string? reason { get; init; }
        public SolverLineStep[] line { get; init; } = Array.Empty<SolverLineStep>();
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
        public int? target_index { get; init; }
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
