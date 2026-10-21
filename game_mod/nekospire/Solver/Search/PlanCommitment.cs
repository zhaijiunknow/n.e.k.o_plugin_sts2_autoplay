namespace CombatSolver;

internal enum PlanCommitmentKind
{
    CopyPower,
    CopyCard,
    PowerCycle,
    PotionChain,
    CrossTurnBenefit,
}

internal enum PlanPayoffEvidenceKind
{
    CardPlayed,
    FreePotionUsed,
    RegisteredPowerBenefit,
}

internal readonly record struct PlanPayoffEvidence(
    PlanPayoffEvidenceKind Kind,
    string SourceId,
    int EarliestTurn);

internal sealed record PlanCommitment(
    PlanCommitmentKind Kind,
    PlanAction[] Prefix,
    int OpenedTurn,
    PlanPayoffEvidence Payoff,
    bool UsesPotion,
    int Priority)
{
    internal int CountRealizedPayoffs(SearchNode node)
    {
        int count = 0;
        for (SearchNode? cursor = node; cursor != null; cursor = cursor.Parent)
        {
            if (cursor.Turn < Payoff.EarliestTurn)
                continue;
            switch (Payoff.Kind)
            {
                case PlanPayoffEvidenceKind.CardPlayed:
                    if (cursor.Action is { Kind: PlanActionKind.PlayCard } action
                        && action.Turn >= Payoff.EarliestTurn
                        && string.Equals(action.CardId, Payoff.SourceId, StringComparison.Ordinal))
                        count++;
                    break;
                case PlanPayoffEvidenceKind.FreePotionUsed:
                    if (cursor.Action is { Kind: PlanActionKind.UsePotion } potion
                        && cursor.Parent != null
                        && string.Equals(potion.PotionId, Payoff.SourceId, StringComparison.Ordinal)
                        && cursor.Snapshot.PotionStrategicCost
                            == cursor.Parent.Snapshot.PotionStrategicCost)
                        count++;
                    break;
                case PlanPayoffEvidenceKind.RegisteredPowerBenefit:
                    if (cursor.PowerCommitment is { RealizedEvidence: > 0 } power
                        && power.HasCard(Payoff.SourceId))
                        count = Math.Max(count, power.RealizedEvidence);
                    break;
                default:
                    throw new InvalidOperationException("未知的计划收益证据类型。");
            }
        }
        return count;
    }
}
