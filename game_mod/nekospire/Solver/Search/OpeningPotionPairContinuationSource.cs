namespace CombatSolver;

internal sealed class OpeningPotionPairContinuationSource : IFrontierContinuationSource
{
    private readonly CombatBeamSolver _builder;

    public bool DeduplicatePrefixes => true;

    internal OpeningPotionPairContinuationSource(SearchPassContext context)
    {
        _builder = new CombatBeamSolver(context.Root, context.DisplayNames,
            context.BattleDamage, context.Policy, context.CancellationToken,
            context.ProgressCallback, context.Policy.Profile,
            potionPolicyOverride: SolverPotionPolicy.RequireAtLeastOne,
            maximumPotionUses: 2);
    }

    public IEnumerable<PlanAction[]> Enumerate()
    {
        PlanAction[] openingPotions = _builder.BuildOpeningPotionActions()
            .Where(action => action.Choice == null)
            .GroupBy(action => action.PotionSlot)
            .Take(2)
            .Select(group => group.First()).ToArray();
        if (openingPotions.Length != 2)
            yield break;
        yield return openingPotions;
        yield return [openingPotions[1], openingPotions[0]];
    }

    public bool CanReplay(PlanAction[] prefix)
        => _builder.CanReplayOpeningPrefix(prefix);
}
