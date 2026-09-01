namespace CombatSolver.Engine.Common;

internal static class EngineDiagnostics
{
    public static void Warn(string message)
        => global::CombatSolver.Entry.Logger?.Warn(message);
}
