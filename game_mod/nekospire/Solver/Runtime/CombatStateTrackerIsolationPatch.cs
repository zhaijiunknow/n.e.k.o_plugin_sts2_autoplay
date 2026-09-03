// RitsuLib-free version of CombatSolver.CombatStateTrackerIsolationPatch. During a background search the
// simulation must never notify the LIVE combat state tracker (that would leak predicted state into the real
// combat). SimulationNotificationIsolation is entered around Solve; this plain-Harmony prefix suppresses the
// notification while active. Registered from CombatSolverRuntime.Install().
using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;

namespace CombatSolver;

[HarmonyPatch(typeof(CombatStateTracker), "NotifyCombatStateChanged", [typeof(string)])]
internal static class CombatStateTrackerIsolationPatch
{
    [HarmonyPriority(Priority.First)]
    public static bool Prefix(string caller)
    {
        if (!SimulationNotificationIsolation.IsActive)
            return true;
        SimulationNotificationIsolation.LogSuppression(caller);
        return false;
    }
}
