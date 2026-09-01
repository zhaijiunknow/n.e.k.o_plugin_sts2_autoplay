using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;

namespace CombatSolver;

internal static class ActEndingBossPolicy
{
    public static bool IsRecoveryFight(CombatState combatState)
    {
        if (combatState.Encounter?.RoomType != RoomType.Boss)
            return false;

        RunState runState = combatState.RunState as RunState
            ?? throw new InvalidOperationException("Boss 战没有可识别的 RunState。");
        if (runState.CurrentActIndex < runState.Acts.Count - 1)
            return true;

        return runState.Act.SecondBossEncounter?.Id == combatState.Encounter.Id;
    }
}
