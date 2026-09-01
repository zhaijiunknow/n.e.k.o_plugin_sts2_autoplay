// RitsuLib-free version of CombatSolver.PowerDynamicVarMaterializationGuardPatch. All Powers must have their
// display DynamicVars materialized during the main-thread root capture before worker-thread simulation; if a
// simulation lazily hits a null _dynamicVars under isolation, throw rather than materialize on a worker thread.
using HarmonyLib;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;

namespace CombatSolver;

[HarmonyPatch(typeof(PowerModel), "get_DynamicVars")]
internal static class PowerDynamicVarMaterializationGuardPatch
{
    private static readonly AccessTools.FieldRef<PowerModel, DynamicVarSet?> DynamicVarsField =
        AccessTools.FieldRefAccess<PowerModel, DynamicVarSet?>("_dynamicVars");

    [HarmonyPriority(Priority.First)]
    public static void Prefix(PowerModel __instance)
    {
        if (SimulationNotificationIsolation.IsActive && DynamicVarsField(__instance) == null)
        {
            throw new InvalidOperationException(
                $"后台模拟尝试惰性创建 Power 显示变量：power={__instance.Id.Entry}；" +
                "该实例必须在主线程根捕获阶段完成物化。");
        }
    }
}
