namespace CombatSolver;

internal readonly record struct SearchRequestWorkSnapshot(
    long ExpandedNodes,
    long TransitionCount,
    long ChoiceBranchesEvaluated)
{
    public static SearchRequestWorkSnapshot ForSingleSolver(
        int expandedNodes,
        int transitionCount,
        int choiceBranchesEvaluated)
    {
        ArgumentOutOfRangeException.ThrowIfNegative(expandedNodes);
        ArgumentOutOfRangeException.ThrowIfNegative(transitionCount);
        ArgumentOutOfRangeException.ThrowIfNegative(choiceBranchesEvaluated);
        return new SearchRequestWorkSnapshot(
            expandedNodes,
            transitionCount,
            choiceBranchesEvaluated);
    }
}

/// <summary>
/// Accumulates Beam-solver work for one coordinator request.
/// A solver contributes exactly once after a normal return, a potion-policy miss, or cancellation.
/// </summary>
internal sealed class SearchRequestWorkTotals
{
    private readonly Lock _gate = new();
    private long _expandedNodes;
    private long _transitionCount;
    private long _choiceBranchesEvaluated;
    private int _recordedSolverCount;

    internal int RecordedSolverCountForTesting
    {
        get
        {
            lock (_gate)
                return _recordedSolverCount;
        }
    }

    public void Record(
        int expandedNodes,
        int transitionCount,
        int choiceBranchesEvaluated)
    {
        SearchRequestWorkSnapshot completedSolver = SearchRequestWorkSnapshot.ForSingleSolver(
            expandedNodes,
            transitionCount,
            choiceBranchesEvaluated);

        lock (_gate)
        {
            _expandedNodes += completedSolver.ExpandedNodes;
            _transitionCount += completedSolver.TransitionCount;
            _choiceBranchesEvaluated += completedSolver.ChoiceBranchesEvaluated;
            _recordedSolverCount++;
        }
    }

    public SearchRequestWorkSnapshot Snapshot()
    {
        lock (_gate)
        {
            return new SearchRequestWorkSnapshot(
                _expandedNodes,
                _transitionCount,
                _choiceBranchesEvaluated);
        }
    }
}
