// Lattice shims for declarations that live in upstream shell files we deliberately do NOT vendor
// (Godot-coupled settings persistence / the UI-facing controller). Each block names its upstream
// origin (CombatSolver 1.0.0, main@1ca3d6ce) so a re-sync can diff it against the source of truth.
// Keeping them here lets the vendored HotKernel / HotSearch / Search / Runtime files resolve with
// zero edits.

// The vendoring script rewrites direct private game-member access into GameRef.* calls
// (game_mod/scripts/vendor-combat-solver.py). A global using keeps that rewrite free of per-file
// `using` edits.
global using CombatSolver.Engine.Common;

using CombatSolver.HotSearch;

namespace CombatSolver;

// ---- src/Runtime/SolverSettings.cs (excluded: `using Godot;`, settings file persistence) ----------

internal enum SolverPotionPolicy
{
    Disabled,
    Smart,
    RequireAtLeastOne,
}

internal enum BossHpStrategy
{
    ProgressionFirst,
    MinimizeHpLoss,
}

/// <summary>
/// Only the member the vendored closure reads (<c>CombatRootSnapshot.Capture</c> falls back to
/// <c>SolverSettings.ResolvePotionThresholds(SolverSettings.Current)</c> when no thresholds are passed).
/// nekospire has no per-potion threshold UI, so the overrides are always empty; the facade passes its
/// thresholds explicitly anyway.
/// </summary>
internal sealed record SolverSettingsData
{
    public Dictionary<string, int>? PotionHpThresholdOverrides { get; init; }
}

internal static class SolverSettings
{
    public static SolverSettingsData Current { get; } = new();

    public static PotionHpThresholds ResolvePotionThresholds(SolverSettingsData data)
        => PotionHpThresholds.FromDictionary(data.PotionHpThresholdOverrides);
}

// ---- src/Runtime/SolverControllerSessions.cs (excluded: belongs to the SolverController shell) ----

internal sealed class SearchProgressDisplayState(long startedAtTick)
{
    public SearchProgressDisplayState() : this(Environment.TickCount64)
    {
    }

    public long StartedAtTick { get; private set; } = startedAtTick;
    public long LastRenderAtTick { get; private set; } = startedAtTick;
    public SolverProgress? RenderedProgress { get; private set; }

    public void Restart(long nowTick)
    {
        StartedAtTick = nowTick;
        LastRenderAtTick = nowTick;
        RenderedProgress = null;
    }

    public bool TryCreate(
        SolverProgress? progress,
        long nowTick,
        out SolverProgress displayProgress)
    {
        if (progress == null
            || nowTick - LastRenderAtTick < HotSearchConstants.ProgressUiIntervalMilliseconds)
        {
            displayProgress = null!;
            return false;
        }

        long elapsedMilliseconds = Math.Max(
            progress.ElapsedMilliseconds,
            Math.Max(
                RenderedProgress?.ElapsedMilliseconds ?? 0L,
                Math.Max(0L, nowTick - StartedAtTick)));
        displayProgress = elapsedMilliseconds == progress.ElapsedMilliseconds
            ? progress
            : progress with { ElapsedMilliseconds = elapsedMilliseconds };
        LastRenderAtTick = nowTick;
        RenderedProgress = displayProgress;
        return true;
    }
}
