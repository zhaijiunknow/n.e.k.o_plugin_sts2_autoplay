namespace CombatSolver;

// T45 shim: this type lives in upstream's Runtime/SolverControllerSessions.cs, which is part of the
// upstream mod shell we deliberately do not vendor (it pulls in the UI-facing SolverController).
// The engine only needs the throttled progress-display helper, so it is reproduced verbatim here.
// Keep byte-compatible behaviour with upstream: elapsed time never regresses and rendering is
// throttled to SolverWeights.ProgressUiIntervalMilliseconds.
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
            || nowTick - LastRenderAtTick < SolverWeights.ProgressUiIntervalMilliseconds)
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
