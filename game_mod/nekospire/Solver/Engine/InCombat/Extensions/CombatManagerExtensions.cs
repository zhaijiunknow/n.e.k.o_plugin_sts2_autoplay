using MegaCrit.Sts2.Core.Combat;

namespace CombatSolver.Engine.InCombat.Extensions;

internal static class CombatManagerExtensions
{
    // Vendored build must not touch CombatManager._turnState (private) / CombatTurnState (internal) —
    // that requires runtime publicization. The game exposes a public DebugOnlyGetState(); use it.
    // The original GetLiveTurnState (returning the internal CombatTurnState) is unused in the closure
    // and is intentionally omitted.
    public static CombatState? GetLiveCombatState(this CombatManager combatManager)
        => combatManager.DebugOnlyGetState();
}
