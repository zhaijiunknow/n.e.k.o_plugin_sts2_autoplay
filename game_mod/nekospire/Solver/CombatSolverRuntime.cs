// Runtime installer for the vendored CombatSolver brain. Called from NekoComm.ModEntry.Initialize().
// The 1.0.0 HotKernel searches on value-type copies of the combat state, so — unlike the 0.50.1 engine,
// which cloned live game models on worker threads — it needs no Harmony isolation patches of its own.
// What remains here: wire the engine's logger, and PatchAll() this assembly's own [HarmonyPatch] types
// (Server/CombatEventTriggerPatch, Game/NekoSettingsPatch).
// RitsuLib is still a hard dependency of the vendored build (HotRootBuilder reads
// RitsuLibFramework.GetMaxHandSize), but it does NOT runtime-publicize the game assembly for this mod, so
// the engine's private game-member access is routed through GameRef (reflection).
// Recommendation only — never deploys.
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
            // Wire a real game logger so the search diagnostics write to godot.log.
            Entry.Logger ??= new MegaCrit.Sts2.Core.Logging.Logger(
                "nekospire-solver", MegaCrit.Sts2.Core.Logging.LogType.Generic);
            var harmony = new Harmony("nekospire_solver");
            // PatchAll() scans the calling assembly for [HarmonyPatch] types. The vendored CombatSolver
            // closure contains none; these are nekospire's own patches.
            harmony.PatchAll();
            PatchesInstalled = true;
            Entry.Logger?.Info("[CombatSolver/Test] NEKOSPIRE_PATCHES installed engine=hot_kernel");
        }
        catch (Exception ex)
        {
            PatchesInstalled = false;
            Entry.Logger?.Info($"[CombatSolver/Test] NEKOSPIRE_PATCHES failed: {ex.Message}");
        }
    }
}
