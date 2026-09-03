// Vendored-entry shim. CombatSolver's real Entry.cs (which `using Godot;` and runs the full mod
// lifecycle) is NOT vendored; the vendored Engine/Search/Prediction closure references
// `CombatSolver.Entry.Logger` and `CombatSolver.Entry.ModId`. A class with the SAME full name lets those
// references resolve with zero edits to the vendored files. Logger is wired by CombatSolverRuntime.Install()
// via RitsuLibFramework.CreateLogger (RitsuLib is a declared dependency of the vendored build).
using MegaCrit.Sts2.Core.Logging;

namespace CombatSolver;

internal static class Entry
{
    public const string ModId = "CombatSolver";

    public static Logger? Logger { get; set; }
}
