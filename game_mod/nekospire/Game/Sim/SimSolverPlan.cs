// Assembles the /solver/plan payload from the new deterministic pipeline:
//   SimBuild.FromLive -> SimSearch.Run (win-prob) -> SolverPlanPayload
// Keeps the existing SolverPlanPayload shape (plus the Phase-4 win-prob/budget fields) so the
// Router/plugin contract is unchanged, and carries honest coverage/warnings for what the model does
// not yet fully cover. Game-coupled (reads CombatState/Player) — the pure model lives in Sim/.
using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using NekoComm.Game.Sim;

namespace NekoComm.Game
{
    public static class SimSolverPlan
    {
        internal static SolverPlanPayload Build(CombatState combat, Player me, SimBudget budget, int horizon = 2, int maxTurnActions = 6)
        {
            var sim = SimBuild.FromLive(combat, me);
            var result = SimSearch.Run(sim, budget, horizon, maxTurnActions);
            var first = result.Line.Count > 0 ? result.Line[0] : (SimAction?)null;

            var steps = BuildSteps(sim, result.Line);
            var (exact, inferred, unsupported, ignored) = ClassifyCoverage(sim);
            var warnings = BuildWarnings(sim);

            return new SolverPlanPayload
            {
                in_combat = true,
                turn = sim.Turn,
                score = result.WinProb,
                line = steps,
                horizon = horizon,
                max_turn_actions = maxTurnActions,
                draw_model = "sim",
                warnings = warnings,
                coverage = new CoverageSummaryPayload { exact = exact, inferred = inferred, unsupported = unsupported, ignored = ignored, potions = sim.Potions.Count, risk = (inferred + unsupported + ignored) > 0 },
                win_prob = result.WinProb,
                search_status = result.Status,
                budget_ms = result.Ms,
                nodes_expanded = result.Nodes,
                rollouts_total = result.Rollouts,
                confidence = result.Rollouts > 0 ? Math.Min(1.0, result.WinProb * result.Rollouts) : 0,
                policy_explanation = BuildPolicyText(sim, first, result.WinProb),
            };
        }

        // Resolve card ids against the REAL evolving state by replaying the chosen actions on a clone.
        // Merely removing the played card is wrong for cards that mutate the hand (e.g. SECOND_WIND
        // exhausts other cards, which would otherwise still look playable). PlayCard applies the full
        // effect, so card_id always refers to the card actually at that index when the action was taken.
        // After the first EndTurn the next draw is unknown, so subsequent ids are null (honest). Steps are
        // grouped per turn, each bucket ending with the turn's "end_turn" step.
        private static SolverTurnStep[] BuildSteps(SimState sim, IReadOnlyList<SimAction> line)
        {
            var turns = new List<SolverTurnStep>();
            var currentSteps = new List<SolverLineStep>();
            var run = sim.Clone();
            var resolved = true;
            int turn = sim.Turn;
            foreach (var a in line)
            {
                string? id = null;
                string? name = null;
                if (a.Kind == SimActionKind.PlayCard)
                {
                    if (resolved && a.CardIndex >= 0 && a.CardIndex < run.Hand.Count)
                    {
                        id = run.Hand[a.CardIndex].Id;
                        name = run.Hand[a.CardIndex].Name;
                    }
                    if (resolved) SimResolver.PlayCard(run, a.CardIndex, a.TargetIndex);   // apply real effect
                }
                if (a.Kind == SimActionKind.EndTurn)
                {
                    id = null;
                    name = null;
                    resolved = false;   // next turn's hand is unknown
                    SimResolver.EndPlayerTurn(run);
                }
                currentSteps.Add(new SolverLineStep
                {
                    kind = a.Kind == SimActionKind.PlayCard ? "play_card"
                        : a.Kind == SimActionKind.EndTurn ? "end_turn" : "use_potion",
                    card_index = a.CardIndex >= 0 ? a.CardIndex : null,
                    card_id = id,
                    card_name = name,
                    target_index = a.TargetIndex,
                });
                if (a.Kind == SimActionKind.EndTurn)
                {
                    turns.Add(new SolverTurnStep { turn = turn, steps = currentSteps.ToArray() });
                    currentSteps = new List<SolverLineStep>();
                    turn++;
                }
            }
            if (currentSteps.Count > 0)
                turns.Add(new SolverTurnStep { turn = turn, steps = currentSteps.ToArray() });
            return turns.ToArray();
        }

        // Coarse coverage: exact = field-driven simple card; inferred = behaviour card (summon/DSL)
        // not fully modelled; ignored = X/star-X cost skipped from search.
        // CombatSolver-style four-way coverage: exact (fully simulated) / inferred (an effect is
        // approximated or a behaviour is not fully represented) / unsupported (a behaviour card with no
        // table entry — the engine cannot simulate it) / ignored (X/star-X cost). Honest per-hand report.
        private static (int exact, int inferred, int unsupported, int ignored) ClassifyCoverage(SimState sim)
        {
            var exact = 0; var inferred = 0; var unsupported = 0; var ignored = 0;
            foreach (var c in sim.Hand)
            {
                if (c.CostsX || c.CostsStarX) ignored++;
                else if (c.BehaviorUnmodeled) unsupported++;   // behaviour card, no transcription
                else if (c.SummonCardId != null || c.OnPlayScript != null || c.ApproximateEffect) inferred++;
                else exact++;
            }
            return (exact, inferred, unsupported, ignored);
        }

        private static string[] BuildWarnings(SimState sim)
        {
            var w = new List<string>();
            if (sim.DrawPile.Count == 0) w.Add("draw_pile_empty:无抽牌堆信息,跨回合手牌未展开");
            if (sim.Hand.Any(c => c.CostsX || c.CostsStarX)) w.Add("ignored_coverage:X/星费卡未建模,不入搜索");
            if (sim.Hand.Any(c => c.SummonCardId != null || c.OnPlayScript != null || c.ApproximateEffect)) w.Add("behavior_card_inferred:行为卡效果仅近似/未完整建模");
            if (sim.Hand.Any(c => c.BehaviorUnmodeled)) w.Add("behavior_card_unsupported:行为卡无效果表项,引擎无法模拟");
            if (sim.Enemies.Any(e => e.Moves.Count == 0)) w.Add("enemy_moves_unknown:敌人招式表未填充,按无意图处理");
            return w.ToArray();
        }

        private static string BuildPolicyText(SimState sim, SimAction? first, double winProb)
        {
            if (first == null) return "无可执行动作。";
            if (first.Value.Kind == SimActionKind.EndTurn) return "结束回合。";
            var card = sim.Hand.Where((_, i) => i == first.Value.CardIndex).FirstOrDefault();
            var name = card?.Name ?? card?.Id ?? "卡";
            return $"打出「{name}」(胜率 {winProb:P0})。";
        }
    }
}
