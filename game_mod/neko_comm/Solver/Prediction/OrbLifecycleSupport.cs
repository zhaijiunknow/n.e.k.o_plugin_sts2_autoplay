using MegaCrit.Sts2.Core.Entities.Players;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver;

internal static class OrbLifecycleSupport
{
    public static void TriggerBeforeTurnEnd(
        CombatPredictionSimulator simulator,
        SimulatedCombatState combat,
        Player player)
    {
        int historyEntryStart = simulator.History.Entries.Count;
        simulator.State.GetPlayerCombatState(player).OrbQueue.BeforeTurnEnd(simulator);
        TriggeredPowerSupport.CompensateHistorySince(simulator, combat, historyEntryStart);
    }
}
