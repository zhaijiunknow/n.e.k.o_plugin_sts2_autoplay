using System.Diagnostics;

namespace CombatSolver;

internal static partial class CombatSearchCoordinator
{
    private const int EarlyTurnExplorationNodeBudget = 1_000_000;
    // Keep a small balanced prefix from every depth, then spend the optional tail only
    // on layers that have already produced a strict improvement. The hard ceiling keeps
    // one promising layer from consuming the whole continuation window.
    private const int EarlyTurnExplorationInitialRankLimit = 4;
    private const int EarlyTurnExplorationRankLimit = 6;

    private static SolverResult RunEarlyTurnExploration(
        SearchPassContext context,
        SolverResult selected)
    {
        CombatRootSnapshot root = context.Root;
        SolverDisplayNames displayNames = context.DisplayNames;
        BattleDamageSnapshot battleDamage = context.BattleDamage;
        SearchPolicySnapshot policy = context.Policy;
        CancellationToken cancellationToken = context.CancellationToken;
        Action<SolverProgress>? progressCallback = context.ProgressCallback;
        Stopwatch requestClock = context.Clock;
        if (policy.IncludeTurnSetup
            || policy.EarlyTurnExplorationDepth == 0
            || selected.ResultScope != SolverResultScope.SearchCompletion)
            return selected;

        string? selectedStopReason = EarlyTurnExplorationStopReason(selected);
        if (selectedStopReason is not null)
        {
            policy.Diagnostics.Info(
                $"[CombatSolver/Test] EARLY_TURN_EXPLORATION skipped=true " +
                $"reason={selectedStopReason} hp_lost={selected.ProjectedBattleHpLost} " +
                $"potions={selected.PotionCount}");
            return selected;
        }

        int RemainingMilliseconds() => policy.EarlyTurnExplorationBudgetMilliseconds
            - (int)requestClock.ElapsedMilliseconds;
        if (RemainingMilliseconds() <= 10_000)
            return selected;
        SearchRequestWorkTotals workTotals = policy.RequestWorkTotals
            ?? throw new InvalidOperationException("早期回合探索缺少请求工作量账本。");
        long expandedAtStart = workTotals.Snapshot().ExpandedNodes;
        long RemainingNodes() => EarlyTurnExplorationNodeBudget
            - (workTotals.Snapshot().ExpandedNodes - expandedAtStart);

        // E1 遥测：只聚合这次探索实际做过的事。侦察消耗、派发的续搜、
        // 每次续搜的最好成绩以及最终汇总全部记在这里；即使改进为零也要记录，
        // 否则无法回答“ETC 花了多少、工作有没有白做”。
        List<EarlyTurnContinuationImprovement> continuationOutcomes = [];
        int attempted = 0;

        SearchPolicySnapshot scoutPolicy = policy with
        {
            NoveltySearch = null,
            UseNoveltyPortfolio = false,
            StopAtAcceptableBattleHpLoss = false,
        };
        SolverSearchProfile scoutProfile = policy.Profile with
        {
            BeamWidth = Math.Min(384, Math.Max(256, policy.Profile.BeamWidth * 4)),
            MaxExpandedNodes = Math.Min(150_000, policy.Profile.MaxExpandedNodes),
            MaxCardBranchesPerNode = Math.Min(128,
                policy.Profile.MaxCardBranchesPerNode * 2),
            MaxHandChoiceBranchesPerAction = Math.Min(96,
                policy.Profile.MaxHandChoiceBranchesPerAction * 2),
            MaxPileChoiceBranchesPerAction = Math.Min(72,
                policy.Profile.MaxPileChoiceBranchesPerAction * 2),
            SoftTimeBudgetMilliseconds = RemainingMilliseconds(),
            StopPortfolioAtHpTarget = false,
        };
        HashSet<(int Turn, StateFingerprint State)> seenFrontiers = [];
        List<EarlyTurnFrontierCandidate> frontiers = [];
        void Observe(int completedTurns, IReadOnlyList<EarlyTurnFrontierCandidate> candidates)
        {
            foreach (EarlyTurnFrontierCandidate candidate in candidates)
            {
                var key = (completedTurns, candidate.StateKey);
                if (seenFrontiers.Add(key))
                    frontiers.Add(candidate);
            }
            policy.Diagnostics.Info(
                $"[CombatSolver/Test] EARLY_TURN_FRONTIER depth={completedTurns} " +
                $"candidates={candidates.Count} unique={frontiers.Count} " +
                $"potion_prefixes={candidates.Count(candidate => candidate.Actions.Any(action => action.Kind == PlanActionKind.UsePotion))}");
        }

        policy.Diagnostics.Info(
            $"[CombatSolver/Test] EARLY_TURN_EXPLORATION start " +
            $"depth={policy.EarlyTurnExplorationDepth} beam={scoutProfile.BeamWidth} " +
            $"nodes={scoutProfile.MaxExpandedNodes} " +
            $"initial_ranks_per_depth={EarlyTurnExplorationInitialRankLimit} " +
            $"max_ranks_per_depth={EarlyTurnExplorationRankLimit} " +
            "rank_extension=after_improvement incumbent_bound=eligible_strict_hp " +
            $"remaining_ms={RemainingMilliseconds()}");
        long scoutExpandedBeforeForMetrics = workTotals.Snapshot().ExpandedNodes;
        int scoutExpanded;
        try
        {
            SolverResult scout = new CombatBeamSolver(root, displayNames, battleDamage,
                scoutPolicy, cancellationToken, progressCallback, scoutProfile,
                earlyTurnScoutDepth: policy.EarlyTurnExplorationDepth,
                earlyTurnScoutObserver: Observe,
                directSearchPurpose: DirectSearchPurpose.EarlyTurnScout).Solve();
            scoutExpanded = (int)Math.Max(0, workTotals.Snapshot().ExpandedNodes
                - scoutExpandedBeforeForMetrics);
            if (IsCompleteVictory(scout)
                && IsBetterPotionPolicyResult(root, policy, scout, selected))
                selected = scout;
        }
        catch (PotionPolicyUnsatisfiedException)
        {
            policy.Diagnostics.Info("[CombatSolver/Test] EARLY_TURN_EXPLORATION scout_potion_policy_unsatisfied=true");
            scoutExpanded = (int)Math.Max(0, workTotals.Snapshot().ExpandedNodes
                - scoutExpandedBeforeForMetrics);
        }

        selectedStopReason = EarlyTurnExplorationStopReason(selected);
        if (selectedStopReason is not null)
            return PublishEarlyTurnExplorationTelemetry(selected, scoutExpanded, selectedStopReason);

        static List<EarlyTurnFrontierCandidate> InterleavePotionStates(
            IEnumerable<EarlyTurnFrontierCandidate> candidates)
        {
            EarlyTurnFrontierCandidate[] withPotion = candidates
                .Where(candidate => candidate.Actions.Any(action => action.Kind == PlanActionKind.UsePotion))
                .ToArray();
            EarlyTurnFrontierCandidate[] withoutPotion = candidates
                .Where(candidate => candidate.Actions.All(action => action.Kind != PlanActionKind.UsePotion))
                .ToArray();
            List<EarlyTurnFrontierCandidate> ordered = [];
            for (int index = 0; index < Math.Max(withPotion.Length, withoutPotion.Length); index++)
            {
                if (index < withPotion.Length) ordered.Add(withPotion[index]);
                if (index < withoutPotion.Length) ordered.Add(withoutPotion[index]);
            }
            return ordered;
        }

        List<EarlyTurnFrontierCandidate>[] layers =
        [
            InterleavePotionStates(frontiers.Where(candidate => candidate.CompletedTurns == 1)),
            InterleavePotionStates(frontiers.Where(candidate => candidate.CompletedTurns == 2)),
        ];
        bool[] extendLayer = new bool[layers.Length];
        FrontierContinuationScheduler continuationScheduler = new(context);
        for (int rank = 0; rank < EarlyTurnExplorationRankLimit; rank++)
        {
            for (int layerIndex = 0; layerIndex < layers.Length; layerIndex++)
            {
                List<EarlyTurnFrontierCandidate> layer = layers[layerIndex];
                if (rank >= layer.Count || RemainingMilliseconds() <= 10_000
                    || RemainingNodes() < 100)
                    continue;
                if (rank >= EarlyTurnExplorationInitialRankLimit && !extendLayer[layerIndex])
                    continue;
                cancellationToken.ThrowIfCancellationRequested();
                if (policy.MemoryPressureSignal.IsEnabled
                    && !policy.MemoryPressureSignal.CanReachCommit(512L * 1024 * 1024))
                    policy.MemoryPressureSignal.ReclaimAndContinue(
                        cancellationToken, "early_turn_continuation");
                EarlyTurnFrontierCandidate frontier = layer[rank];
                bool usesPotion = frontier.Actions.Any(action => action.Kind == PlanActionKind.UsePotion);
                PrimarySearchIncumbent? continuationIncumbent = SelectEarlyTurnContinuationIncumbent(
                    BuildPrimarySearchIncumbent(root, policy, selected),
                    policy.TheftPolicy == SolverTheftPolicy.PreserveResources,
                    policy.PotionStrategy.EvaluateForcedUses(
                        selected.BestNode.Actions, renewablePotionShapedRock: false).AllForcedUsesSatisfied,
                    selected.BestNode.Actions.Any(action => action.Kind == PlanActionKind.UsePotion),
                    usesPotion,
                    selected.Snapshot.StrategicHpCredit != 0,
                    selected.Snapshot.HasRisk);
                SolverSearchProfile continuationProfile = policy.Profile with
                {
                    MaxExpandedNodes = (int)Math.Min(RemainingNodes(),
                        Math.Min(120_000, policy.Profile.MaxExpandedNodes)),
                    SoftTimeBudgetMilliseconds = RemainingMilliseconds() - 5_000,
                };
                long continuationExpandedBefore = workTotals.Snapshot().ExpandedNodes;
                long continuationElapsedBefore = requestClock.ElapsedMilliseconds;
                SolverResult? candidate = continuationScheduler.DispatchOptional(
                    new ContinuationSearchRequest(context,
                        ContinuationPurpose.EarlyTurnContinuation,
                        frontier.Actions, continuationProfile,
                        usesPotion ? SolverPotionPolicy.RequireAtLeastOne : null,
                        null, null)
                    {
                        PrimaryIncumbent = continuationIncumbent,
                    },
                    "EARLY_TURN_CONTINUATION");
                attempted++;
                if (candidate == null)
                {
                    // 即使用药政策在完整搜索前否决该派发，也记录一次尝试；耗时和
                    // 展开值都取实测差值，可以为零，不伪造路线成绩。
                    continuationOutcomes.Add(new EarlyTurnContinuationImprovement(
                        frontier.CompletedTurns, rank,
                        requestClock.ElapsedMilliseconds - continuationElapsedBefore,
                        workTotals.Snapshot().ExpandedNodes - continuationExpandedBefore,
                        Completed: false, Improved: false, Won: null,
                        Loss: null, Potions: null, EndedTurn: null));
                    policy.Diagnostics.Info(
                        $"[CombatSolver/Test] EARLY_TURN_CONTINUATION " +
                        $"depth={frontier.CompletedTurns} rank={rank} " +
                        $"completed=false outcome=unavailable " +
                        $"elapsed_ms={requestClock.ElapsedMilliseconds - continuationElapsedBefore} " +
                        $"expanded={workTotals.Snapshot().ExpandedNodes - continuationExpandedBefore}");
                    continue;
                }
                if (candidate.ResultScope != SolverResultScope.SearchCompletion)
                {
                    continuationOutcomes.Add(new EarlyTurnContinuationImprovement(
                        frontier.CompletedTurns, rank,
                        requestClock.ElapsedMilliseconds - continuationElapsedBefore,
                        workTotals.Snapshot().ExpandedNodes - continuationExpandedBefore,
                        Completed: false, Improved: false,
                        Won: null,
                        Loss: null,
                        Potions: null,
                        EndedTurn: null));
                    policy.Diagnostics.Info(
                        $"[CombatSolver/Test] EARLY_TURN_CONTINUATION " +
                        $"depth={frontier.CompletedTurns} rank={rank} " +
                        $"completed=false outcome={candidate.ResultScope} " +
                        $"elapsed_ms={requestClock.ElapsedMilliseconds - continuationElapsedBefore} " +
                        $"expanded={workTotals.Snapshot().ExpandedNodes - continuationExpandedBefore}");
                    // 保留原有交接结果，只给它附加纯值遥测。
                    return PublishEarlyTurnExplorationTelemetry(candidate, scoutExpanded, "adopted");
                }
                bool improved = IsCompleteVictory(candidate)
                    && policy.PotionStrategy.EvaluateForcedUses(
                        candidate.BestNode.Actions, renewablePotionShapedRock: false)
                    .AllForcedUsesSatisfied
                    && IsBetterPotionPolicyResult(root, policy, candidate, selected);
                if (improved)
                {
                    selected = candidate;
                    if (rank < EarlyTurnExplorationInitialRankLimit)
                        extendLayer[layerIndex] = true;
                }
                // E1 遥测：逐 rank 记录这次续搜的最好成绩。注意这里记录的是
                // “候选自己的成绩”，是否击败 incumbent 只看 improved。
                continuationOutcomes.Add(new EarlyTurnContinuationImprovement(
                    frontier.CompletedTurns, rank,
                    requestClock.ElapsedMilliseconds - continuationElapsedBefore,
                    workTotals.Snapshot().ExpandedNodes - continuationExpandedBefore,
                    Completed: true, Improved: improved,
                    Won: IsCompleteVictory(candidate),
                    Loss: candidate.ProjectedBattleHpLost,
                    Potions: candidate.PotionCount,
                    EndedTurn: candidate.CombatEndedTurn));
                policy.Diagnostics.Info(
                    $"[CombatSolver/Test] EARLY_TURN_CONTINUATION " +
                    $"depth={frontier.CompletedTurns} rank={rank} " +
                    $"opening={frontier.Actions[0].Kind}:{frontier.Actions[0].CardId}:{frontier.Actions[0].PotionId} " +
                    $"prefix_potions={frontier.Actions.Count(action => action.Kind == PlanActionKind.UsePotion)} " +
                    $"prefix={string.Join(',', frontier.Actions.Take(12).Select(action => action.Kind switch
                    {
                        PlanActionKind.UsePotion => $"P:{action.PotionId}",
                        PlanActionKind.PlayCard => $"C:{action.CardId}" +
                            (action.Choice is { Cards.Count: > 0 } choice
                                ? $"[{string.Join('+', choice.Cards.Select(card => card.CardId))}]" : ""),
                        _ => "E",
                    }))} " +
                    $"won={IsCompleteVictory(candidate)} " +
                    $"hp_lost={candidate.ProjectedBattleHpLost} " +
                    $"potions={candidate.PotionCount} selected={improved} " +
                    $"incumbent_hp={continuationIncumbent?.StrategicHpDeficit.ToString() ?? "-"} " +
                    $"incumbent_pruned={candidate.PrimaryIncumbentBranchesPruned} " +
                    $"incumbent_certified_healing_bound_pruned={candidate.PrimaryIncumbentCertifiedHealingBoundBranchesPruned} " +
                    $"elapsed_ms={requestClock.ElapsedMilliseconds - continuationElapsedBefore} " +
                    $"expanded={workTotals.Snapshot().ExpandedNodes - continuationExpandedBefore}");
            }
            selectedStopReason = EarlyTurnExplorationStopReason(selected);
            if (RemainingMilliseconds() <= 10_000
                || RemainingNodes() < 100
                || selectedStopReason is not null)
                break;
        }
        bool rankLimitReached = false;
        for (int layerIndex = 0; layerIndex < layers.Length; layerIndex++)
        {
            int layerRankLimit = extendLayer[layerIndex]
                ? EarlyTurnExplorationRankLimit
                : EarlyTurnExplorationInitialRankLimit;
            if (layers[layerIndex].Count > layerRankLimit)
            {
                rankLimitReached = true;
                break;
            }
        }
        string stop = EarlyTurnExplorationStopReason(selected)
            ?? (RemainingMilliseconds() <= 10_000
                ? "time"
                : RemainingNodes() < 100
                    ? "nodes"
                    : rankLimitReached
                        ? "rank_limit"
                        : "ranks_exhausted");
        return PublishEarlyTurnExplorationTelemetry(selected, scoutExpanded, stop);

        string? EarlyTurnExplorationStopReason(SolverResult result)
        {
            if (IsProvenZeroDamageRoute(root, policy, result))
                return "zero_damage";
            return HasReachedAcceptableBattleHpLoss(policy, result)
                ? "acceptable_target"
                : null;
        }

        // E1 遥测的唯一出口：把这次探索的侦察消耗、续搜明细和终止原因
        // 汇总到结果对象上。selected 已经由调用者决定，这里只做纯值记录，
        // 不改变任何排名、剪枝或返回路径。
        SolverResult PublishEarlyTurnExplorationTelemetry(
            SolverResult result, int scoutNodes, string stopReason)
        {
            int improvements = 0;
            int? bestLoss = null;
            int? bestPotions = null;
            int? bestEndedTurn = null;
            int? firstImprovementDepth = null;
            int? firstImprovementRank = null;
            foreach (EarlyTurnContinuationImprovement outcome in continuationOutcomes)
            {
                if (!outcome.Improved)
                    continue;
                if (outcome.Loss is not { } loss || outcome.Potions is not { } potions)
                    throw new InvalidOperationException(
                        "改进的早期回合续搜缺少战损或药水结果。");
                improvements++;
                firstImprovementDepth ??= outcome.CompletedTurns;
                firstImprovementRank ??= outcome.Rank;
                if (bestLoss is null)
                {
                    bestLoss = loss;
                    bestPotions = potions;
                    bestEndedTurn = outcome.EndedTurn;
                }
                else if (bestPotions is not { } incumbentPotions)
                    throw new InvalidOperationException(
                        "早期回合最佳战损缺少对应的药水数量。");
                else if (loss < bestLoss.Value
                    || loss == bestLoss.Value && potions < incumbentPotions)
                {
                    bestLoss = loss;
                    bestPotions = potions;
                    bestEndedTurn = outcome.EndedTurn;
                }
            }
            long expanded = workTotals.Snapshot().ExpandedNodes - expandedAtStart;
            result.EarlyTurnExploration = new EarlyTurnExplorationTelemetry(
                scoutNodes, frontiers.Count, attempted, improvements,
                bestLoss, bestPotions, bestEndedTurn,
                firstImprovementDepth, firstImprovementRank,
                expanded,
                stopReason,
                continuationOutcomes.AsReadOnly());
            string firstImprovement = firstImprovementDepth is { } depth
                && firstImprovementRank is { } rank
                    ? $"{depth}:{rank}"
                    : "none";
            policy.Diagnostics.Info(
                $"[CombatSolver/Test] EARLY_TURN_EXPLORATION result " +
                $"frontiers={frontiers.Count} attempted={attempted} improvements={improvements} " +
                $"first_improvement={firstImprovement} " +
                $"result_scope={result.ResultScope} won={IsCompleteVictory(result)} " +
                $"expanded={expanded} node_limit={EarlyTurnExplorationNodeBudget} stop={stopReason}");
            return result;
        }
    }

    internal static PrimarySearchIncumbent? SelectEarlyTurnContinuationIncumbent(
        PrimarySearchIncumbent? incumbent,
        bool preserveResources,
        bool selectedForcedUsesSatisfied,
        bool selectedHasExplicitPotionUses,
        bool prefixUsesPotion,
        bool selectedHasStrategicCredit,
        bool selectedHasRisk)
    {
        // BuildPrimarySearchIncumbent already excludes incomplete victories, growth,
        // relic targets and death saves. Keep the no-potion eligibility search intact
        // when the selected route used a potion: a potion-bearing fixed prefix has
        // no no-potion branch and its member already uses RequireAtLeastOne.
        if (incumbent is null || preserveResources || !selectedForcedUsesSatisfied
            || selectedHasStrategicCredit || selectedHasRisk
            || selectedHasExplicitPotionUses && !prefixUsesPotion)
            return null;

        // Equal strategic HP can still improve raw loss, potion use or other tie
        // breakers. Disable the turn cutoff so the external bound only prunes a
        // strictly worse certified HP floor. The member's existing potion baseline
        // and its own incumbent-tightening rules remain unchanged.
        return incumbent.Value with { CombatEndedTurn = int.MaxValue };
    }
}
