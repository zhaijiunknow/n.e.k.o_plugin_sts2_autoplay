// Vendored-entry shim. CombatSolver's real Entry (upstream src/Runtime/Entry.cs: `using Godot;`, runs the
// full standalone-mod lifecycle) is NOT vendored; the vendored closure references
// `CombatSolver.Entry.Logger`. A class with the SAME full name lets those references resolve with zero
// edits to the vendored files. Logger is wired by CombatSolverRuntime.Install() to a game Logger
// (upstream's is its own CombatSolverLog; the vendored files only call Info/Debug/Warn/Error on it).
using MegaCrit.Sts2.Core.Logging;

namespace CombatSolver;

internal static class Entry
{
    public const string ModId = "CombatSolver";

    public static Logger? Logger { get; set; }
}
