using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver;

internal static class PlayerTurnEndLifecycle
{
    public static void RunPhaseOne(
        CombatPredictionSimulator simulator,
        SimulatedCombatState combat,
        Player player,
        IReadOnlyList<Creature> participants)
    {
        EndTurnPowerSupport.TriggerVeryEarly(combat, participants);
        TurnStartRelicSupport.TriggerBeforeSideTurnEnd(simulator, combat, participants);
        OrbLifecycleSupport.TriggerBeforeTurnEnd(simulator, combat, player);
        simulator.SimulateEndPlayerTurnAfterOrbPassives();
        CorePowerSupport.CompletePlayerEarlySideTurnEndEffects(combat, participants);
    }
}
