// Turn-START capture hook. CombatStateTracker.NotifyCombatStateChanged fires on every live combat-state
// transition; on the true Start-phase transition we capture the CombatRootSnapshot into TurnSetupRootHolder so
// /solver/plan can solve from the turn-setup phase with IncludeTurnSetup=true. Skipped while a background
// simulation is running (predicted state must never leak into the live capture). Discovered by the same
// Harmony.PatchAll() in CombatSolverRuntime.Install() as the other isolation patches. Reflection-only, no RitsuLib.
using System;
using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;
using NekoComm.Game;

namespace NekoComm.Game
{
    [HarmonyPatch(typeof(CombatStateTracker), "NotifyCombatStateChanged", [typeof(string)])]
    internal static class TurnSetupCapturePatch
    {
        [HarmonyPriority(Priority.Last)]
        public static void Postfix(string caller)
        {
            if (CombatSolver.SimulationNotificationIsolation.IsActive)
                return;
            TurnSetupRootHolder.Refresh();
        }
    }
}
