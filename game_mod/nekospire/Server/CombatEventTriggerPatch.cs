// Real-time combat-transition hook. The game calls CombatStateTracker.NotifyCombatStateChanged when
// combat state changes (start/end/turn/etc.). This postfix nudges GameEventService.EvaluateNow() so the
// changed state is diffed and broadcast to /events/stream subscribers immediately, instead of waiting for
// the (now slower) fallback poll. The vendored solver (CombatSolver 1.0.0 HotKernel) searches on
// value-type copies and never drives live game objects, so every call seen here is a real transition.
// Discovered by CombatSolverRuntime.Install()'s Harmony.PatchAll().
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
