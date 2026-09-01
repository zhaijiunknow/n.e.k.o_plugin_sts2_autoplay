// Runtime installer for the vendored CombatSolver brain. Called from NekoComm.ModEntry.Initialize().
// RitsuLib is NOT used: the two isolation patches are plain Harmony and registered here; the direct private
// game-access is routed through GameRef (reflection) so it runs without runtime publicization. Recommendation
// only — never deploys.
using System;
using HarmonyLib;

namespace CombatSolver;

internal static class CombatSolverRuntime
{
    public static bool PatchesInstalled { get; private set; }

    public static void Install()
    {
        try
        {
            // Wire a real game logger so the search/mirror diagnostics write to godot.log.
            Entry.Logger ??= new MegaCrit.Sts2.Core.Logging.Logger(
                "neko-comm-solver", MegaCrit.Sts2.Core.Logging.LogType.Generic);
            var harmony = new Harmony("neko_comm_solver");
            // PatchAll() scans the calling assembly for [HarmonyPatch] types — exactly our two isolation
            // patches (the vendored CombatSolver code contains no other [HarmonyPatch] types).
            harmony.PatchAll();
            PatchesInstalled = true;
            Entry.Logger?.Info("[CombatSolver/Test] SOLVER_ISOLATION_PATCHES installed");
        }
        catch (Exception ex)
        {
            PatchesInstalled = false;
            Entry.Logger?.Info($"[CombatSolver/Test] SOLVER_ISOLATION_PATCHES failed: {ex.Message}");
        }
    }

    public static bool IsolationGuaranteed => PatchesInstalled;
}
