// Seam adapter between the N.E.K.O mod's HTTP contract (SolverPlanPayload) and the vendored CombatSolver
// search brain. RitsuLib is installed so the game assembly is runtime-publicized and the faithful search
// runs. Capture on the game thread, offload the search to a worker thread, map SolverResult -> payload.
// Recommendation only — never deploys.
using System;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using CombatSolver;

namespace NekoComm.Game;

internal static class CombatSolverFacade
{
    const int ShortBudgetMilliseconds = 3000;
    const int DeepBudgetMilliseconds = 3000;

    public static async Task<SolverPlanPayload> BuildPlanAsync(
        CombatState state,
        Player me,
        SolverFacadeOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        options ??= SolverFacadeOptions.Default;

        CombatRootSnapshot root;
        SolverDisplayNames names;
        BattleDamageSnapshot damage;
        try
        {
            root = CombatRootSnapshot.Capture(state);
            if (!ReferenceEquals(root.PlayerIdentity, me))
                return Failed("solver_failed", "捕获的玩家与本地玩家不一致。");
            names = SolverDisplayNames.Capture(state);
            damage = BattleDamageTracker.Observe(state);
        }
        catch (Exception ex)
        {
            return Failed("capture_failed", $"CombatSolver 捕获真机状态失败: {ex.Message}\n{ex.StackTrace}");
        }

        // Potion tolerance gate (production policy): search NO-potion FIRST. If the no-potion route's
        // battle damage is within the effective tolerance (default 8, +6 when the player holds Burning
        // Blood), lock the no-potion route. Otherwise ESCALATE by potion count (1 potion, then 2, ...),
        // locking the FEWEST potions that bring damage within tolerance — never spend more potions than
        // needed for the damage the run can absorb.
        var tolerance = new SolverPotionTolerance(options.DamageThreshold, HasBurningBlood(me));
        var potionFreePolicy = BuildPolicy(options, SolverPotionPolicy.Disabled);
        SolverResult result;
        try
        {
            var potionFree = await RunSolveAsync(root, names, damage, potionFreePolicy, cancellationToken);
            if (tolerance.IsWithinTolerance(potionFree.Snapshot.CumulativePlayerHpLost))
                return MapResult(potionFree, me, options, root);   // acceptable damage -> no potions

            var smartPolicy = BuildPolicy(options, SolverPotionPolicy.Smart);
            int potionSlots = me.Potions.Count();
            SolverResult best = potionFree;
            for (int n = 1; n <= potionSlots; n++)
            {
                var bounded = await RunSolveAsync(
                    () => new CombatBeamSolver(root, names, damage, smartPolicy, cancellationToken, null,
                        searchProfile: options.ShortBudgetMilliseconds is int s ? SolverSearchProfile.Short with { SoftTimeBudgetMilliseconds = s } : SolverSearchProfile.Short,
                        shortCheckpointMilliseconds: null,
                        potionPolicyOverride: SolverPotionPolicy.Smart,
                        potionFreePolicyBaseline: null, maximumPotionUses: n).Solve());

                if (tolerance.IsWithinTolerance(bounded.Snapshot.CumulativePlayerHpLost))
                    return MapResult(bounded, me, options, root);   // fewest potions that hit tolerance
                if (bounded.BestNode.Score > best.BestNode.Score)
                    best = bounded;                            // keep the best we found so far
            }
            result = best;
        }
        catch (Exception ex)
        {
            string inner = ex.InnerException == null ? "" : $"\n内层: {ex.InnerException.Message}\n{ex.InnerException.StackTrace}";
            return Failed("solver_failed", $"CombatSolver 搜索失败: {ex.Message}{inner}\n{ex.StackTrace}");
        }

        return MapResult(result, me, options, root);
    }

    private static async Task<SolverResult> RunSolveAsync(
        CombatRootSnapshot root, SolverDisplayNames names, BattleDamageSnapshot damage,
        SearchPolicySnapshot policy, CancellationToken ct)
        => await Task.Run(
            () => CombatSearchCoordinator.Solve(root, names, damage, policy, ct, null), ct);

    private static async Task<SolverResult> RunSolveAsync(
        Func<SolverResult> solve)
        => await Task.Run(solve, default);

    private static bool HasBurningBlood(Player me)
        => me.Relics.Any(relic =>
            string.Equals(relic.Id.Entry, "BURNING_BLOOD", StringComparison.OrdinalIgnoreCase));

    private static SearchPolicySnapshot BuildPolicy(SolverFacadeOptions options, SolverPotionPolicy potionPolicy)
    {
        var shortProfile = options.ShortBudgetMilliseconds is int s
            ? SolverSearchProfile.Short with { SoftTimeBudgetMilliseconds = s }
            : SolverSearchProfile.Short;
        var deepProfile = options.DeepBudgetMilliseconds is int d
            ? SolverSearchProfile.Deep with { SoftTimeBudgetMilliseconds = d }
            : SolverSearchProfile.Deep;

        var diagnostics = new SearchDiagnosticsSink(
            msg => Entry.Logger?.Info(msg),
            msg => Entry.Logger?.Debug(msg));
        var frame = new SearchFramePressureSignal();
        var memory = new SearchMemoryPressureSignal();
        memory.Disable();

        return new SearchPolicySnapshot(
            ShortProfile: shortProfile,
            DeepProfile: deepProfile,
            PotionPolicy: potionPolicy,
            PotionStrategy: new PotionStrategySnapshot(potionPolicy, System.Array.Empty<PotionSlotDirective>()),
            DetailedDiagnostics: false,
            VerifyIncrementalSearch: false,
            ForceShortOnly: options.ForceShortOnly,
            MeasurePhasePerformance: false,
            MaxDegreeOfParallelism: options.MaxDegreeOfParallelism,
            ShortBudgetOverrideMilliseconds: options.ShortBudgetMilliseconds,
            DeepBudgetOverrideMilliseconds: options.DeepBudgetMilliseconds,
            IncludeTurnSetup: false,
            TheftPolicy: null,
            ActTransitionBossHpStrategy: BossHpStrategy.ProgressionFirst,
            FinalBossHpStrategy: BossHpStrategy.ProgressionFirst,
            Diagnostics: diagnostics,
            FramePressureSignal: frame,
            MemoryPressureSignal: memory);
    }

    private static SolverPlanPayload MapResult(SolverResult result, Player me, SolverFacadeOptions options, CombatRootSnapshot root)
    {
        var first = result.BestNode.Actions.FirstOrDefault();
        // Group the forecasted line into per-turn buckets; each turn's steps end with an "end_turn"
        // boundary (the engine emits EndTurn as the last action of a turn). Only the current turn's
        // card_index is filled (later turns' hand positions are unknown — card_id only). The next move is
        // line[0].steps[0]; there is no duplicated top-level action/card_index/card_id/target_index.
        SolverTurnStep[] line = result.BestNode.Actions
            .GroupBy(a => a.Turn)
            .Select(g => new SolverTurnStep
            {
                turn = g.Key,
                steps = g.Select(a => MapStep(a, me)).ToArray()
            })
            .ToArray();

        (int exact, int inferred, int unsupported, int ignored) = ClassifyCoverage(result);
        var warnings = BuildWarnings(result);

        return new SolverPlanPayload
        {
            in_combat = true,
            turn = result.Snapshot.Turn,
            score = result.BestNode.Score,
            state_fingerprint = StateFingerprint(root),
            line = line,
            beam_width = SolverSearchProfile.Short.BeamWidth,
            horizon = result.SearchedTurns,
            max_turn_actions = SolverSearchProfile.Deep.MaxCardBranchesPerNode,
            draw_model = "combatsolver",
            warnings = warnings,
            coverage = new CoverageSummaryPayload
            {
                exact = exact,
                inferred = inferred,
                unsupported = unsupported,
                ignored = ignored,
                potions = result.PotionCount,
                risk = result.Snapshot.HasRisk || !result.Forecast.IsExactForModeledDamage,
            },
            win_prob = null,
            search_status = MapStatus(result),
            budget_ms = (long)result.Elapsed.TotalMilliseconds,
            nodes_expanded = result.ExpandedNodes,
            rollouts_total = null,
            confidence = null,
            policy_explanation = BuildPolicyText(first),
        };
    }

    private static string StateFingerprint(CombatRootSnapshot root) =>
        // Deterministic short hash of the captured combat state, so a consumer can tell at a glance
        // whether an action changed the position (different fingerprint => recompute). Uses the capture's
        // canonical ContinuationStamp state text.
        System.Convert.ToHexString(
                System.Security.Cryptography.SHA256.HashData(
                    System.Text.Encoding.UTF8.GetBytes(root.ContinuationStamp.StateText)))
            [..16];

    private static SolverLineStep MapStep(PlanAction a, Player me)
    {
        string kind = a.Kind == PlanActionKind.PlayCard ? "play_card"
            : a.Kind == PlanActionKind.UsePotion ? "use_potion"
            : "end_turn";
        int? cardIndex = a.Turn == me.PlayerCombatState?.TurnNumber ? MapStepCardIndex(a, me) : null;
        return new SolverLineStep
        {
            kind = kind,
            card_index = cardIndex,
            card_id = a.Kind == PlanActionKind.UsePotion ? a.PotionId : a.CardId,
            target_index = a.TargetIndex >= 0 ? a.TargetIndex : null,
        };
    }

    private static int? MapStepCardIndex(PlanAction a, Player me)
    {
        var hand = me.PlayerCombatState?.Hand.Cards;
        if (hand == null || a.Kind != PlanActionKind.PlayCard)
            return null;
        for (var i = 0; i < hand.Count; i++)
            if (string.Equals(hand[i].Id.Entry, a.CardId, StringComparison.Ordinal))
                return i;
        return null;
    }

    private static (int exact, int inferred, int unsupported, int ignored) ClassifyCoverage(SolverResult result)
    {
        int handCount = result.Snapshot.HandCount;
        int inferred = result.Snapshot.PredictionGaps.Count(g => g.Compensated)
            + result.Forecast.ApproximationDetails.Count;
        int unsupported = result.Snapshot.PredictionGaps.Count(g => !g.Compensated)
            + result.Forecast.UnsupportedDetails.Count;
        int ignored = 0;
        int exact = Math.Max(0, handCount - inferred - unsupported - ignored);
        return (exact, inferred, unsupported, ignored);
    }

    private static string[] BuildWarnings(SolverResult result)
    {
        var warnings = new System.Collections.Generic.List<string>();
        if (result.BoundaryReason != SearchBoundaryReason.None)
            warnings.Add($"search_boundary:{result.BoundaryReason.ToString().ToLowerInvariant()}");
        foreach (var gap in result.Snapshot.PredictionGaps)
            warnings.Add(gap.Compensated ? $"inferred:{gap.SourceId}.{gap.Method}" : $"unsupported:{gap.SourceId}.{gap.Method}");
        foreach (var detail in result.Forecast.UnsupportedDetails)
            warnings.Add($"enemy_unsupported:{detail}");
        foreach (var detail in result.Forecast.ApproximationDetails)
            warnings.Add($"enemy_approximation:{detail}");
        return warnings.ToArray();
    }

    private static string MapStatus(SolverResult result)
        => result.BoundaryReason == SearchBoundaryReason.None ? "complete"
            : result.BoundaryReason == SearchBoundaryReason.TimeLimit ? "budget_exceeded"
            : $"boundary_{result.BoundaryReason.ToString().ToLowerInvariant()}";

    private static string BuildPolicyText(PlanAction? first)
    {
        if (first is null)
            return "无可执行动作。";
        if (first.Kind == PlanActionKind.EndTurn)
            return "结束回合。";
        if (first.Kind == PlanActionKind.UsePotion)
            return $"使用药水「{(string.IsNullOrEmpty(first.PotionTitle) ? first.PotionId : first.PotionTitle)}」。";
        return $"打出「{(string.IsNullOrEmpty(first.CardTitle) ? first.CardId : first.CardTitle)}」。";
    }

    private static SolverPlanPayload Failed(string reason, string message) => new()
    {
        in_combat = false,
        warnings = new[] { message },
    };
}

internal sealed class SolverFacadeOptions
{
    public static SolverFacadeOptions Default { get; } = new()
    {
        ForceShortOnly = true,
        ShortBudgetMilliseconds = 3000,
        DeepBudgetMilliseconds = 3000,
        MaxDegreeOfParallelism = 1,
        DamageThreshold = 8,
    };
    public bool ForceShortOnly { get; init; }
    public int? ShortBudgetMilliseconds { get; init; }
    public int? DeepBudgetMilliseconds { get; init; }
    public int MaxDegreeOfParallelism { get; init; }

    /// <summary>Base battle-damage tolerance (default 8): if the no-potion route loses at most this many
    /// HP, it is accepted and potions are not searched. +6 when the player holds Burning Blood.</summary>
    public int DamageThreshold { get; init; }
}

/// <summary>Production potion-tolerance policy. Searches NO-potion first; accepts it while the route's
/// battle damage stays within an effective threshold (base threshold + Burning Blood heal).</summary>
internal sealed class SolverPotionTolerance
{
    public const int BurningBloodHeal = 6;

    public int BaseThreshold { get; }
    public bool HasBurningBlood { get; }
    public int EffectiveThreshold => BaseThreshold + (HasBurningBlood ? BurningBloodHeal : 0);

    public SolverPotionTolerance(int baseThreshold, bool hasBurningBlood)
    {
        BaseThreshold = baseThreshold;
        HasBurningBlood = hasBurningBlood;
    }

    /// <summary>Pure recompute helper. Given a potion budget and a damage threshold, returns the effective
    /// battle-damage tolerance (threshold + Burning Blood heal). potionCount is the potion budget at the
    /// decision point; the current formula applies the heal regardless of how many potions are held.</summary>
    public int Recompute(int potionCount, int damageThreshold)
    {
        _ = potionCount;
        return damageThreshold + (HasBurningBlood ? BurningBloodHeal : 0);
    }

    public bool IsWithinTolerance(int noPotionDamage)
        => noPotionDamage <= EffectiveThreshold;
}
