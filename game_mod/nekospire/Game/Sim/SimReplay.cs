// Replay a recorded action sequence from a pre-state and diff against the captured post-state.
// Pure / game-type-free so it is unit-testable. Phase 0 covers player-turn card plays + end turn;
// enemy moves and RNG outcomes are injected as events once the enemy engine lands (Phase 2), so the
// replay contract here is deliberately narrow and honest.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    public enum SimRecordedKind { PlayCard, EndTurn, UsePotion }

    /// <summary>One captured action. Mirrors the shape SimCapture emits so replay == capture.</summary>
    public readonly struct SimRecordedAction
    {
        public readonly SimRecordedKind Kind;
        public readonly int CardIndex;    // hand index for PlayCard
        public readonly int? TargetIndex; // enemy index for PlayCard
        public SimRecordedAction(SimRecordedKind kind, int cardIndex = -1, int? targetIndex = null)
        {
            Kind = kind; CardIndex = cardIndex; TargetIndex = targetIndex;
        }
    }

    public static class SimReplay
    {
        /// <summary>Apply <paramref name="actions"/> from <paramref name="pre"/> and diff the result
        /// against <paramref name="expectedPost"/>. Returns empty list if they match.</summary>
        public static List<string> ReplayAndDiff(SimState pre, IReadOnlyList<SimRecordedAction> actions, SimState expectedPost)
        {
            var run = pre.Clone();
            foreach (var a in actions)
            {
                switch (a.Kind)
                {
                    case SimRecordedKind.PlayCard:
                        SimResolver.PlayCard(run, a.CardIndex, a.TargetIndex);
                        break;
                    case SimRecordedKind.EndTurn:
                        SimResolver.EndPlayerTurn(run);
                        // Enemy phase + next player turn are Phase 2; Phase 0 replay stays single-turn.
                        break;
                    case SimRecordedKind.UsePotion:
                        // Potions land in Phase 1+; no-op here (honest).
                        break;
                }
            }
            SimDiff.Canonicalize(run);
            SimDiff.Canonicalize(expectedPost);
            return SimDiff.Diff(run, expectedPost);
        }
    }
}
