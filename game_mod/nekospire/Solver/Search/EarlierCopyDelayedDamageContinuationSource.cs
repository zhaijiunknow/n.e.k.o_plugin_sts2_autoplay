namespace CombatSolver;

internal sealed class EarlierCopyDelayedDamageContinuationSource : IFrontierContinuationSource
{
    private readonly CombatBeamSolver _builder;
    private readonly IReadOnlyList<PlanAction> _route;

    public bool DeduplicatePrefixes => true;

    internal EarlierCopyDelayedDamageContinuationSource(
        SearchPassContext context,
        IReadOnlyList<PlanAction> route)
    {
        _builder = new CombatBeamSolver(context.Root, context.DisplayNames,
            context.BattleDamage, context.Policy, context.CancellationToken,
            context.ProgressCallback, context.Policy.Profile);
        _route = route;
    }

    public IEnumerable<PlanAction[]> Enumerate()
        => _builder.BuildEarlierCopyPotionDelayedDamagePrefixes(_route);

    // The builder replays each prefix before returning it.
    public bool CanReplay(PlanAction[] prefix) => true;
}
