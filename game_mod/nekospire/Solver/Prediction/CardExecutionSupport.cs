using MegaCrit.Sts2.Core.Combat.History.Entries;
using MegaCrit.Sts2.Core.Entities.Creatures;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver;

internal static class CardExecutionSupport
{
    public static bool AutoPlay(
        CombatPredictionSimulator simulator,
        SimulatedCombatState combat,
        PredictedCard card,
        Creature? target,
        ISet<uint> processedEnemyDeaths,
        bool payResources = false,
        string? nestedChoiceSourceId = null)
    {
        int historyStart = simulator.History.Entries.Count;
        using IDisposable scope = combat.BeginCardExecutionScope(processedEnemyDeaths);
        if (payResources)
            simulator.PaidAutoPlay(card, target, nestedChoiceSourceId);
        else
            simulator.AutoPlay(card, target, nestedChoiceSourceId: nestedChoiceSourceId);

        CombatPredictionCardPlayStartedEntry? started = null;
        foreach (CombatPredictionHistoryEntry entry in simulator.History.EntriesFrom(historyStart))
        {
            if (entry is not CombatPredictionCardPlayStartedEntry candidate
                || !ReferenceEquals(candidate.CardPlay.Card, card.Preview))
            {
                continue;
            }
            started = candidate;
            break;
        }
        if (started == null)
            return false;
        return nestedChoiceSourceId == null || !simulator.HasPendingChoice;
    }
}
