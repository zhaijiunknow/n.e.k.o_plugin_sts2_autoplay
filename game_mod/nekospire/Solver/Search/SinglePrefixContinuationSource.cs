namespace CombatSolver;

internal sealed class SinglePrefixContinuationSource(PlanAction[] prefix)
    : IFrontierContinuationSource
{
    public bool DeduplicatePrefixes => false;

    public IEnumerable<PlanAction[]> Enumerate()
    {
        yield return prefix;
    }

    public bool CanReplay(PlanAction[] candidate) => true;
}
