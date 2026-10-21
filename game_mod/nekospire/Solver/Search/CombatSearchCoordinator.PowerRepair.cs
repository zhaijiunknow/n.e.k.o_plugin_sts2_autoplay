namespace CombatSolver;

internal static partial class CombatSearchCoordinator
{
    private static PlanAction[]? BuildRevealedPowerRepairPrefix(
        CombatBeamSolver builder, IReadOnlyList<PlanAction> openings, SolverSearchProfile profile)
    {
        foreach (PlanAction opening in openings.Take(3))
        {
            HashSet<string> available = builder.BuildPowerActionsAfterPrefix([opening])
                .Select(action => action.CardStateKey).ToHashSet(StringComparer.Ordinal);
            foreach (PlanAction setup in builder.BuildOpeningHandSetupActions([opening],
                         maximumActions: profile.MaxHandChoiceBranchesPerAction))
            {
                PlanAction? power = builder.BuildPowerActionsAfterPrefix([opening, setup])
                    .FirstOrDefault(action => PowerCardValuationModels.Registry.ContainsCardId(action.CardId!)
                        && !available.Contains(action.CardStateKey));
                if (power != null)
                    return [opening, setup, power];
            }
        }
        return null;
    }

    private static PlanAction[]? FindForcedPowerTurnBoundary(
        CombatRootSnapshot root, SearchPolicySnapshot policy, CombatBeamSolver builder,
        SolverResult candidate)
    {
        IReadOnlyList<PlanAction> actions = candidate.BestNode.Actions;
        for (int count = 1; count <= actions.Count; count++)
        {
            PlanAction action = actions[count - 1];
            if (action.Kind != PlanActionKind.EndTurn || action.Turn < root.StartTurnNumber + 1)
                continue;
            PlanAction[] prefix = actions.Take(count).ToArray();
            if (policy.PotionStrategy.EvaluateForcedUses(prefix, root.HasRenewablePotionShapedRock)
                .AllForcedUsesSatisfied && builder.CanContinueAtPrefix(prefix))
                return prefix;
        }
        return null;
    }

    // Existing detached turn outcomes locate the first remaining loss. Never retain
    // a search-node graph or replay the complete plan just to choose a repair window.
    private static PlanAction[]? FindFirstLaterLossRepairPrefix(SolverResult candidate, int warmPrefixCount)
    {
        if (!IsCompleteVictory(candidate) || candidate.Snapshot.HasRisk)
            return null;
        IReadOnlyList<PlanAction> actions = candidate.BestNode.Actions;
        if (warmPrefixCount <= 0 || warmPrefixCount > actions.Count)
            return null;
        candidate.AssertCompleteTurnOutcomes();
        int warmTurn = actions[warmPrefixCount - 1].Turn;
        int losingTurn = candidate.HpLostByTurn.Where(pair => pair.Key >= warmTurn && pair.Value > 0)
            .Select(pair => pair.Key).DefaultIfEmpty(-1).Min();
        if (losingTurn < 0)
            return null;
        PlanAction[] prefix = actions.TakeWhile(action => action.Turn < losingTurn).ToArray();
        return prefix.LastOrDefault() is { } last
            && (last.Kind == PlanActionKind.EndTurn || last.EndsPlayerTurn) ? prefix : null;
    }
}
