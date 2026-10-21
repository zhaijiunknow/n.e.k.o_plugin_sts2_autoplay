namespace CombatSolver;

internal static partial class CombatSearchCoordinator
{
    private static SolverResult RunGeneratedPotionChainSearchPass(
        SearchPassContext context, SolverResult baseline)
    {
        if (baseline.ResultScope != SolverResultScope.SearchCompletion
            || context.Policy.PotionPolicy == SolverPotionPolicy.Disabled
            || context.Policy.Interaction?.CurrentTakeoverRequest != null
            || IsCompleteVictory(baseline) && baseline.ProjectedBattleHpLost == 0
            || !context.Root.SearchablePotions.Any(potion =>
                PotionValuationRegistry.Default.GeneratesPotionChain(potion.PotionId))
            || !context.Budget.RequestWindow(context.Profile).CanStart(8_000))
            return baseline;

        PlanAction[] route = baseline.BestNode.Actions.ToArray();
        CombatBeamSolver builder = new(context.Root, context.DisplayNames,
            context.BattleDamage, context.Policy, context.CancellationToken,
            context.ProgressCallback, context.Profile);
        List<PlanAction[]> anchors = [];
        int firstEndTurn = Array.FindIndex(route, action => action.Kind == PlanActionKind.EndTurn);
        PlanAction? earlyPaidPotion = route.FirstOrDefault(action =>
            action.Kind == PlanActionKind.UsePotion
            && action.Turn <= context.Root.StartTurnNumber + 1
            && !PotionValuationRegistry.Default.GeneratesPotionChain(action.PotionId));
        if (firstEndTurn > 0 && earlyPaidPotion != null)
        {
            foreach (PlanAction attack in builder.BuildOpeningOffensiveFollowUps([route[0]]))
            {
                PlanAction[] variant = [route[0], attack,
                    .. route.Skip(1).Take(firstEndTurn)];
                if (!builder.CanReplayOpeningPrefix(variant))
                    continue;
                PlanAction? potion = builder.BuildPotionActionsAfterPrefix(variant)
                    .FirstOrDefault(action => action.PotionSlot == earlyPaidPotion.PotionSlot
                        && action.PotionId == earlyPaidPotion.PotionId);
                if (potion != null)
                    anchors.Add([.. variant, potion]);
            }
        }
        foreach (var item in route.Select((action, index) => (action, index))
                     .Where(item => item.action.Kind == PlanActionKind.UsePotion
                         && item.action.Turn <= context.Root.StartTurnNumber + 1
                         && !PotionValuationRegistry.Default.GeneratesPotionChain(
                             item.action.PotionId))
                     .Take(2))
            anchors.Add(route.Take(item.index + 1).ToArray());
        if (firstEndTurn >= 0)
            anchors.Add(route.Take(firstEndTurn + 1).ToArray());
        anchors.Add([]);
        FrontierContinuationScheduler scheduler = new(context);
        SolverResult selected = baseline;
        int attempted = 0;
        int generators = 0;
        foreach (PlanAction[] anchor in anchors)
        {
            if (attempted >= 2 || !context.Budget.RequestWindow(context.Profile).CanStart(8_000))
                break;
            PlanAction? generator = builder.BuildPotionActionsAfterPrefix(anchor)
                .FirstOrDefault(action => PotionValuationRegistry.Default.GeneratesPotionChain(
                    action.PotionId));
            if (generator == null)
                continue;
            generators++;
            PlanAction[] generatedPrefix = [.. anchor, generator];
            foreach (PlanAction first in builder.BuildFreeEntropicPotionActionsAfterPrefix(
                         generatedPrefix))
            {
                if (attempted >= 2)
                    break;
                PlanAction[] prefix = [.. generatedPrefix, first];
                PlanAction? second = builder.BuildFreeEntropicPotionActionsAfterPrefix(prefix)
                    .FirstOrDefault(action => action.PotionSlot != first.PotionSlot);
                if (second != null)
                    prefix = [.. prefix, second];
                SearchBudgetWindow window = context.Budget.RequestWindow(context.Profile);
                if (!window.CanStart(8_000))
                    break;
                SolverSearchProfile memberProfile = window.Limit(context.Profile,
                    maximumNodes: 50_000, maximumMilliseconds: 15_000,
                    reserveMilliseconds: 2_000);
                int potionUses = prefix.Count(action => action.Kind == PlanActionKind.UsePotion);
                PlanCommitment plan = new(
                    PlanCommitmentKind.PotionChain,
                    prefix,
                    prefix[0].Turn,
                    new PlanPayoffEvidence(PlanPayoffEvidenceKind.FreePotionUsed,
                        first.PotionId!, first.Turn),
                    UsesPotion: true,
                    Priority: 1);
                SolverResult? candidate = scheduler.DispatchOptional(
                    new ContinuationSearchRequest(context,
                        ContinuationPurpose.GeneratedPotionChain, prefix, memberProfile,
                        SolverPotionPolicy.RequireAtLeastOne, potionUses, potionUses)
                    {
                        Commitment = plan,
                    },
                    "GeneratedPotionChain");
                attempted++;
                if (candidate == null)
                    continue;
                if (candidate.ResultScope != SolverResultScope.SearchCompletion)
                    return candidate;
                PopulateSingleSessionTotals(candidate);
                bool improved = IsBetterPotionPolicyResult(
                    context.Root, context.Policy, candidate, selected);
                context.Policy.Diagnostics.Info(
                    $"[CombatSolver/Test] GENERATED_POTION_CHAIN turn={generator.Turn} " +
                    $"potions={potionUses} won={IsCompleteVictory(candidate)} " +
                    $"hp_lost={candidate.ProjectedBattleHpLost} " +
                    $"expanded={candidate.ExpandedNodes} selected={improved}");
                if (improved)
                    selected = candidate;
            }
            if (attempted > 0)
                break;
        }
        context.Policy.Diagnostics.Info(
            $"[CombatSolver/Test] GENERATED_POTION_CHAIN_DISCOVERY " +
            $"anchors={anchors.Count} generators={generators} attempted={attempted} " +
            $"selected_hp_lost={selected.ProjectedBattleHpLost}");
        return selected;
    }
}
