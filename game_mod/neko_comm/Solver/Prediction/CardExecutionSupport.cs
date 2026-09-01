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
        CardPlayPowerSuppression suppression = combat.SuppressHistorySensitiveCardModifiers(card);
        using IDisposable scope = combat.BeginCardExecutionScope(processedEnemyDeaths);
        try
        {
            if (payResources)
                simulator.PaidAutoPlay(card, target, nestedChoiceSourceId);
            else
                simulator.AutoPlay(card, target, nestedChoiceSourceId: nestedChoiceSourceId);
        }
        finally
        {
            combat.RestoreHistorySensitiveCardModifiers(suppression);
        }

        CombatPredictionCardPlayStartedEntry? started = simulator.History.Entries
            .Skip(historyStart)
            .OfType<CombatPredictionCardPlayStartedEntry>()
            .FirstOrDefault(entry => ReferenceEquals(entry.CardPlay.Card, card.Preview));
        if (started == null)
            return false;
        return nestedChoiceSourceId == null || !simulator.HasPendingChoice;
    }
}
