using MegaCrit.Sts2.Core.Hooks;
using MegaCrit.Sts2.Core.Models;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver.Engine.InCombat.Mirrors;

internal static partial class HookMirrors
{
    /// <summary>
    /// Mirrors <see cref="Hook.ModifyOrbValue" />：球数值修正只在本进程的镜像监听器序列上分发，
    /// 不再把预测态 ICombatState 交回原生 Hook 的监听器枚举（#182/T022）。
    /// </summary>
    public static decimal ModifyOrbValue(
        CombatPredictionSimulator simulator,
        OrbModel orb,
        decimal amount)
    {
        decimal modified = amount;
        foreach (AbstractModel listener in IterateCombatHookListeners(simulator, MirroredHookMask.ModifyOrbValue))
            modified = listener.ModifyOrbValue(orb, modified);
        return modified;
    }
}
