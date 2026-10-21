using System.Collections.Immutable;

namespace CombatSolver;

internal readonly record struct DeferredCopyPlanRule(
    PlanChoiceEffect Effect,
    int PayoffTurnDelay);

internal sealed class PlanMechanismRegistry
{
    internal static PlanMechanismRegistry Default { get; } = new(
    [
        new(PlanChoiceEffect.Nightmare, PayoffTurnDelay: 1),
    ]);

    private readonly ImmutableArray<DeferredCopyPlanRule> _deferredCopies;

    private PlanMechanismRegistry(IEnumerable<DeferredCopyPlanRule> deferredCopies)
    {
        _deferredCopies = deferredCopies.ToImmutableArray();
        if (_deferredCopies.Select(rule => rule.Effect).Distinct().Count()
            != _deferredCopies.Length)
            throw new InvalidOperationException("延后复制计划效果重复登记。");
    }

    internal ImmutableArray<DeferredCopyPlanRule> DeferredCopies => _deferredCopies;
}
