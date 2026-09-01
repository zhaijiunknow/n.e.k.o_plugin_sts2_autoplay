// Win-probability search: replaces the old beam + hand-tuned linear score with a value that is the
// expected P(victory). Player nodes take the max over legal actions; after a player ends their turn,
// an enemy chance node averages the outcome over sampled enemy turns (MonsterAi stream); at the
// horizon a rollout estimates the value; the whole thing is cut off by SimBudget. Pure / game-free.
using System;
using System.Collections.Generic;
using System.Diagnostics;

namespace NekoComm.Game.Sim
{
    public enum SimActionKind { PlayCard, EndTurn }

    public readonly struct SimAction
    {
        public readonly SimActionKind Kind;
        public readonly int CardIndex;
        public readonly int? TargetIndex;
        public SimAction(SimActionKind kind, int cardIndex = -1, int? targetIndex = null)
        {
            Kind = kind; CardIndex = cardIndex; TargetIndex = targetIndex;
        }
        public override string ToString()
            => Kind == SimActionKind.PlayCard ? $"play[{CardIndex}->{TargetIndex}]" : "end_turn";
    }

    public sealed class SimSearchResult
    {
        public IReadOnlyList<SimAction> Line = Array.Empty<SimAction>();
        public double WinProb;
        public string Status = "complete";
        public int Nodes;
        public int Rollouts;
        public long Ms;
    }

    public static class SimSearch
    {
        // Enemy moves follow a deterministic cycle (SimEnemy.RollMove), so a chance node has one
        // outcome — sampling more than once is redundant. Keep 1 sample for speed.
        private const int ENEMY_SAMPLES = 1;
        // Deep rollouts so the leaf value resolves to a real win/loss, not 0.5. 40 plies ≈ 20 player
        // turns, enough to reach a decisive outcome for most combats. Shallow rollouts left every leaf
        // at "undetermined" 0.5 and gave the search no signal.
        private const int ROLLOUT_PLIES = 40;
        private const int ROLLOUT_CARDS_PER_TURN = 5;

        public static SimSearchResult Run(SimState state, SimBudget budget, int horizon = 2, int maxTurnActions = 6)
        {
            var sw = Stopwatch.StartNew();
            var (v, line) = PlayerValue(state, maxTurnActions, horizon, budget);
            sw.Stop();
            return new SimSearchResult
            {
                Line = line != null ? line : Array.Empty<SimAction>(),
                WinProb = v,
                Status = budget.Exceeded ? "budget_exceeded" : "complete",
                Nodes = budget.Nodes,
                Rollouts = budget.Rollouts,
                Ms = sw.ElapsedMilliseconds,
            };
        }

        // ---- player decision node (max) --------------------------------------

        private static (double value, List<SimAction>? line) PlayerValue(SimState state, int turnActionsLeft, int horizon, SimBudget budget)
        {
            budget.TickNode();
            if (budget.Exceeded) return (0.5, null);

            if (IsTerminal(state)) return (TerminalValue(state), null);
            if (horizon <= 0) return (Rollout(state, budget), null);

            var acts = LegalActions(state, turnActionsLeft);
            if (acts.Count == 0) acts.Add(new SimAction(SimActionKind.EndTurn));

            var bestVal = -1.0;
            List<SimAction>? bestLine = null;
            foreach (var act in acts)
            {
                var child = state.Clone();
                ApplyAction(child, act);

                double v;
                List<SimAction>? childLine;
                if (act.Kind == SimActionKind.PlayCard)
                    (v, childLine) = PlayerValue(child, turnActionsLeft - 1, horizon, budget);
                else // EndTurn -> enemy chance node
                    (v, childLine) = EnemyValue(child, horizon - 1, budget);

                if (bestLine == null || v > bestVal)
                {
                    bestVal = v;
                    bestLine = new List<SimAction> { act };
                    if (childLine != null) bestLine.AddRange(childLine);
                }
                if (budget.Exceeded) break;
            }
            return (bestVal, bestLine);
        }

        // ---- enemy chance node (expectation) ---------------------------------

        private static (double value, List<SimAction>? line) EnemyValue(SimState state, int horizon, SimBudget budget)
        {
            budget.TickRollout();
            if (budget.Exceeded) return (0.5, null);

            double sum = 0;
            List<SimAction>? bestLine = null;
            var bestV = -1.0;
            var count = 0;
            for (var i = 0; i < ENEMY_SAMPLES; i++)
            {
                var clone = state.Clone();
                SimEnemy.RunEnemyTurn(clone);
                SimResolver.NewTurn(clone);   // enemy turn done -> start the next player turn
                var (v, line) = PlayerValue(clone, /*turnActionsLeft*/ SimResolver.DefaultHandSize, horizon, budget);
                sum += v;
                count++;
                if (bestLine == null || v > bestV) { bestV = v; bestLine = line; }
                if (budget.Exceeded) break;
            }
            return count > 0 ? (sum / count, bestLine) : (0.5, null);
        }

        // ---- rollout value at the horizon ------------------------------------

        // Rollout to (near) terminal using a greedy play policy so the value resolves to a real
        // win/loss most of the time, rather than stalling at 0.5. Random play rarely finds a decisive
        // outcome, which is why the leaf values were indistinguishable.
        private static double Rollout(SimState state, SimBudget budget)
        {
            budget.TickRollout();
            var s = state.Clone();
            for (var ply = 0; ply < ROLLOUT_PLIES; ply++)
            {
                if (budget.Exceeded) return 0.5;
                if (IsTerminal(s)) return TerminalValue(s);

                var guard = 0;
                while (guard++ < ROLLOUT_CARDS_PER_TURN && !IsTerminal(s))
                {
                    var plays = LegalActions(s, 10).FindAll(a => a.Kind == SimActionKind.PlayCard);
                    if (plays.Count == 0) break;
                    ApplyAction(s, GreedyPick(s, plays));
                }

                if (IsTerminal(s)) return TerminalValue(s);
                SimResolver.EndPlayerTurn(s);
                SimEnemy.RunEnemyTurn(s);
                if (!IsTerminal(s)) SimResolver.NewTurn(s);
            }
            return IsTerminal(s) ? TerminalValue(s) : 0.5;
        }

        // Pick the play that makes the most "progress": kills an enemy (heavily weighted), then deals
        // the most damage, then gains the most block. This is the rollout policy only; the search
        // objective remains win-probability.
        private static SimAction GreedyPick(SimState s, List<SimAction> plays)
        {
            var best = plays[0];
            var bestScore = double.MinValue;
            foreach (var a in plays)
            {
                var c = s.Clone();
                ApplyAction(c, a);
                var score = 0.0;
                for (var i = 0; i < s.Enemies.Count; i++)
                {
                    var before = s.Enemies[i].Hp;
                    var after = i < c.Enemies.Count ? c.Enemies[i].Hp : 0;
                    score += before - after;
                }
                score += c.Players.Count > 0 ? c.Players[0].Block * 0.5 : 0;
                if (IsTerminal(c) && TerminalValue(c) == 1) score += 1e9;
                if (score > bestScore) { bestScore = score; best = a; }
            }
            return best;
        }

        // ---- actions ---------------------------------------------------------

        public static List<SimAction> LegalActions(SimState state, int turnActionsLeft)
        {
            var acts = new List<SimAction>();
            for (var i = 0; i < state.Hand.Count; i++)
            {
                var card = state.Hand[i];
                if (!SimResolver.IsPlayable(state, card)) continue;
                // CombatSolver-consistent: never "confidently" play a behaviour card the engine cannot
                // simulate (no transcription) — that would find a win on a wrong state. Exclude it.
                if (card.BehaviorUnmodeled) continue;
                if (card.Target == SimTargetKind.AnyEnemy)
                {
                    for (var j = 0; j < state.Enemies.Count; j++)
                        if (state.Enemies[j].Alive) acts.Add(new SimAction(SimActionKind.PlayCard, i, j));
                }
                else
                {
                    acts.Add(new SimAction(SimActionKind.PlayCard, i, null));
                }
            }
            // End turn is always an option (the max only picks it when it is best).
            acts.Add(new SimAction(SimActionKind.EndTurn));
            return acts;
        }

        private static void ApplyAction(SimState state, SimAction a)
        {
            if (a.Kind == SimActionKind.PlayCard) SimResolver.PlayCard(state, a.CardIndex, a.TargetIndex);
            else SimResolver.EndPlayerTurn(state);
        }

        private static bool IsTerminal(SimState state)
        {
            var enemiesDead = state.Enemies.Count > 0 && state.Enemies.TrueForAll(e => !e.Alive);
            var playerDead = state.Players.TrueForAll(p => !p.Alive);
            return enemiesDead || playerDead;
        }

        private static double TerminalValue(SimState state)
        {
            var enemiesDead = state.Enemies.Count > 0 && state.Enemies.TrueForAll(e => !e.Alive);
            if (enemiesDead) return 1;
            return 0; // player dead
        }
    }
}
