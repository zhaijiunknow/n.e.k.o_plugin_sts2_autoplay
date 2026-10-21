namespace CombatSolver;

internal sealed class TurnBoundaryContinuationSource : IFrontierContinuationSource
{
    private readonly IEnumerable<PlanAction[]> _anchors;
    private readonly Func<PlanAction[], string> _key;

    public bool DeduplicatePrefixes => false;

    internal TurnBoundaryContinuationSource(
        IEnumerable<PlanAction[]> anchors,
        Func<PlanAction[], string> key)
    {
        _anchors = anchors;
        _key = key;
    }

    public IEnumerable<PlanAction[]> Enumerate()
        => _anchors.DistinctBy(_key).Take(8)
            .Where(prefix => prefix.LastOrDefault()?.Kind == PlanActionKind.EndTurn);

    public bool CanReplay(PlanAction[] prefix) => true;
}
