// Real-time combat-transition hook. The game calls CombatStateTracker.NotifyCombatStateChanged when
// combat state changes (start/end/turn/etc.). This postfix nudges GameEventService.EvaluateNow() so the
// changed state is diffed and broadcast to /events/stream subscribers immediately, instead of waiting for
// the (now slower) fallback poll. Skipped while a background simulation is running so predicted combat
// state never leaks into the live event stream. Discovered by CombatSolverRuntime.Install()'s
// Harmony.PatchAll().
using System;
using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;

namespace NekoComm.Server
{
    [HarmonyPatch(typeof(CombatStateTracker), "NotifyCombatStateChanged", [typeof(string)])]
    internal static class CombatEventTriggerPatch
    {
        public static void Postfix(string caller)
        {
            // The solver isolation prefix fields this same call during a background search; never leak
            // predicted combat state into the live event stream.
            if (CombatSolver.SimulationNotificationIsolation.IsActive)
                return;

            try
            {
                GameEventService.Instance.EvaluateNow();
            }
            catch (Exception ex)
            {
                MegaCrit.Sts2.Core.Logging.Log.Warn(
                    $"[NekoComm.CombatEvent] failed to evaluate events: {ex.Message}");
            }
        }
    }
}
