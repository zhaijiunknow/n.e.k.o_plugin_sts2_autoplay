namespace CombatSolver;

// T45 shim: upstream deleted the old static SolverSearchProfile.Short / .Deep presets (only Default
// survives, SearchPolicySnapshot now takes a single Profile). Our facade still needs a cheap
// "shallow/fast" and a "deep" configuration, so they live here, derived from Default so that a
// future upstream tuning of Default still flows through.
internal static class SolverProfilePresets
{
    /// <summary>Fast/bounded profile used by the potion-bounded retries and by ForceShortOnly.</summary>
    public static SolverSearchProfile Short { get; } = SolverSearchProfile.Default with
    {
        BeamWidth = 40,
        MaxExpandedNodes = 30_000,
        SoftTimeBudgetMilliseconds = 60_000,
    };

    /// <summary>Deep profile used for the primary plan search.</summary>
    public static SolverSearchProfile Deep { get; } = SolverSearchProfile.Default with
    {
        BeamWidth = 80,
        SoftTimeBudgetMilliseconds = 180_000,
    };
}
