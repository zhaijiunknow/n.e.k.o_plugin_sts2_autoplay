namespace CombatSolver;

internal enum OpeningCandidatePurpose
{
    GeneratedPowerCard,
    DelayedDamageCopyPotion,
}

internal readonly record struct OpeningCandidateRule(
    OpeningCandidatePurpose Purpose,
    PlanActionKind ActionKind,
    string Id);

internal sealed class OpeningActionRegistry
{
    internal static OpeningActionRegistry Default { get; } = new(
    [
        new(OpeningCandidatePurpose.GeneratedPowerCard, PlanActionKind.PlayCard, "WHITE_NOISE"),
        new(OpeningCandidatePurpose.DelayedDamageCopyPotion, PlanActionKind.UsePotion, "DUPLICATOR"),
    ]);

    private readonly IReadOnlyDictionary<OpeningCandidatePurpose, OpeningCandidateRule> _rules;

    internal OpeningActionRegistry(IEnumerable<OpeningCandidateRule> rules)
        => _rules = rules.ToDictionary(rule => rule.Purpose);

    internal bool MatchesId(OpeningCandidatePurpose purpose, string id)
        => string.Equals(_rules[purpose].Id, id, StringComparison.Ordinal);

    internal bool Matches(OpeningCandidatePurpose purpose, PlanAction action)
    {
        OpeningCandidateRule rule = _rules[purpose];
        return action.Kind == rule.ActionKind
            && MatchesId(purpose, rule.ActionKind == PlanActionKind.UsePotion
                ? action.PotionId ?? ""
                : action.CardId ?? "");
    }
}
