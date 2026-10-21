namespace CombatSolver;

internal static class PlanHorizonPolicy
{
    internal static bool ShouldExtend(
        int turnsWithoutProgress,
        int ordinaryLimit,
        int deckCycleTurns,
        bool payoffRealized)
        => payoffRealized
            && turnsWithoutProgress >= ordinaryLimit
            && (long)turnsWithoutProgress < (long)ordinaryLimit + deckCycleTurns;
}
