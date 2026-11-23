// Seam adapter between the N.E.K.O mod's HTTP contract (SolverPlanPayload) and the vendored CombatSolver
// search brain (upstream 1.0.0: HotKernel pure-value combat kernel + HotSearch). The combat root is
// captured on the game thread (the only place that reads live game objects — private members go through
// GameRef reflection), the search runs on a worker thread over value-type state only, and the resulting
// SolverResult is mapped onto the payload. Recommendation only — never deploys.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using NekoComm.Game.Sim;
using CombatSolver;
using CombatSolver.HotSearch;

namespace NekoComm.Game;

internal static class CombatSolverFacade
{
    public static async Task<SolverPlanPayload> BuildPlanAsync(
        CombatState state,
        Player me,
        SolverFacadeOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        options ??= SolverFacadeOptions.Default;

        // Multiplayer (co-op): the kernel plans for the LOCAL player only. The player count is frozen
        // into the root's rules (enemy HP / move-block scaling); teammates are not simulated — their
        // future plays are unknowable — so the plan is "what I should do if nobody else acts". The
        // payload says so in its warnings (see BuildWarnings).

        // Same precondition upstream applies before it adopts a result: only the local player's Play phase
        // has a hand to plan for. Anything else is "not my turn yet" — the caller waits and asks again.
        if (state.CurrentSide != CombatSide.Player || me.PlayerCombatState?.Phase != PlayerTurnPhase.Play)
            return Failed("not_play_phase", "CombatSolver 只能在玩家出牌阶段计算。");

        CombatRootSnapshot root;
        SolverDisplayNames names;
        BattleDamageSnapshot damage;
        SearchPolicySnapshot policy;
        // Card-side coverage denominator: the LIVE hand at root-capture time (same moment and same set as
        // the captured root snapshot — the hand this plan's current turn plays from). Null => the hand
        // could not be read, and coverage is then reported as explicitly unknown (-1), never as a fake 0.
        SimCoverageCard[]? handCoverage = null;
        try
        {
            // Engine-default potion economics: a potion is spent only when it saves at least its HP
            // threshold (PotionHpThresholds: 9 / 14 / 18 by tier). This replaces the facade's former
            // "accept <= 8 HP loss, else escalate by potion count" loop — the 1.0.0 search prices potions
            // inside one objective instead of vetoing them after the fact.
            PotionHpThresholds thresholds = PotionHpThresholds.Default;
            root = CombatRootSnapshot.Capture(state, predictPotionReward: false, potionThresholds: thresholds);
            if (!ReferenceEquals(root.PlayerIdentity, me))
                return Failed("solver_failed", "捕获的玩家与本地玩家不一致。");
            names = SolverDisplayNames.Capture(state);
            damage = BattleDamageTracker.Observe(state);
            policy = BuildPolicy(state, options, thresholds);
            try
            {
                handCoverage = CaptureHandCoverage(me);
            }
            catch (Exception ex)
            {
                // Non-fatal: coverage degrades to "unknown" instead of killing the plan.
                Entry.Logger?.Debug($"[NekoSpire] 手牌覆盖捕获失败: {ex.Message}");
            }
        }
        catch (Exception ex)
        {
            return Failed("capture_failed", $"CombatSolver 捕获真机状态失败: {ex.Message}\n{ex.StackTrace}");
        }

        SolverResult result;
        try
        {
            result = await Task.Run(() => Solve(root, names, damage, policy, cancellationToken), cancellationToken);
        }
        catch (Exception ex)
        {
            string inner = ex.InnerException == null ? "" : $"\n内层: {ex.InnerException.Message}\n{ex.InnerException.StackTrace}";
            return Failed("solver_failed", $"CombatSolver 搜索失败: {ex.Message}{inner}\n{ex.StackTrace}");
        }

        return MapResult(result, me, policy, root, handCoverage, state.Players.Count);
    }

    private static SolverResult Solve(
        CombatRootSnapshot root, SolverDisplayNames names, BattleDamageSnapshot damage,
        SearchPolicySnapshot policy, CancellationToken ct)
    {
        // Same scheduling as upstream's search worker: the coordinator thread yields to the game.
        Thread worker = Thread.CurrentThread;
        ThreadPriority previousPriority = worker.Priority;
        worker.Priority = ThreadPriority.BelowNormal;
        try
        {
            return HotSearchCoordinator.Solve(root, names, damage, policy, ct);
        }
        finally
        {
            worker.Priority = previousPriority;
        }
    }

    // Mirrors upstream SolverController.CaptureSearchPolicy with a default SolverSettingsData (Medium
    // preset, Smart potions, ProgressionFirst bosses, relic strategy on, no growth budgets), so
    // /solver/plan searches with the same authority as the standalone mod's own auto-turn-search. The one
    // knob nekospire exposes is the think-time ceiling.
    private static SearchPolicySnapshot BuildPolicy(
        CombatState state, SolverFacadeOptions options, PotionHpThresholds thresholds)
    {
        var diagnostics = new SearchDiagnosticsSink(
            msg => Entry.Logger?.Info(msg),
            msg => Entry.Logger?.Debug(msg));

        return new SearchPolicySnapshot(
            Profile: SolverSearchProfile.Default,
            PotionPolicy: SolverPotionPolicy.Smart,
            PotionStrategy: new PotionStrategySnapshot(SolverPotionPolicy.Smart, Array.Empty<PotionSlotDirective>())
            {
                Thresholds = thresholds,
            },
            DetailedDiagnostics: false,
            VerifyIncrementalSearch: false,
            FixedBudget: false,
            MeasurePhasePerformance: false,
            MaxDegreeOfParallelism: options.MaxDegreeOfParallelism,
            BudgetOverrideMilliseconds: null,
            // The root is always a live Play-phase capture (gated above), so there is no pending turn
            // setup to expand.
            IncludeTurnSetup: false,
            TheftPolicy: TheftEncounterStrategy.IsApplicable(state) ? SolverTheftPolicy.PreserveResources : null,
            ActTransitionBossHpStrategy: BossHpStrategy.ProgressionFirst,
            FinalBossHpStrategy: BossHpStrategy.ProgressionFirst,
            AcceptableBattleHpLoss: 0,
            Diagnostics: diagnostics)
        {
            Act3BossStrategy = SearchPolicySnapshot.IsAct3BossEncounter(
                state.RunState.CurrentActIndex, state.Encounter?.Id.Entry),
            RelicTargets = RelicCounterCatalog.Capture(state, enabled: true, rules: Array.Empty<RelicCounterRule>()),
            GrowthOpportunityTargets = GrowthOpportunityPolicy.Capture(state),
            // Wall-clock ceiling for the adaptive Stage 2/3 deepening on hard fights; easy turns finish
            // in Stage 1 well below it. The engine clamps this to 1..15 s.
            MaxHardFightThinkSeconds = options.MaxThinkSeconds,
        };
    }

    private static SolverPlanPayload MapResult(
        SolverResult result, Player me, SearchPolicySnapshot policy, CombatRootSnapshot root,
        IReadOnlyList<SimCoverageCard>? handCoverage, int playerCount)
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

        SimCoverageSummary coverageSummary = SummarizeCoverage(result, handCoverage);
        var warnings = BuildWarnings(result, coverageSummary, handCoverage);
        if (playerCount > 1)
        {
            // Not a prediction gap of a card: a scope statement. Teammates' actions, and the shared RNG
            // they consume (shuffles, random targets), are outside the model, so later turns of the line
            // are a forecast under "teammates do nothing".
            warnings = warnings.Append($"multiplayer:teammates_not_modeled:players={playerCount}").ToArray();
        }
        int expandedNodes = (int)Math.Min(int.MaxValue, Math.Max(result.ExpandedNodes, result.TotalExpandedNodes));

        return new SolverPlanPayload
        {
            in_combat = true,
            turn = result.Snapshot.Turn,
            score = result.BestNode.Score,
            state_fingerprint = StateFingerprint(root),
            line = line,
            beam_width = policy.Profile.BeamWidth,
            horizon = result.SearchedTurns,
            max_turn_actions = policy.Profile.MaxCardBranchesPerNode,
            draw_model = "combatsolver",
            warnings = warnings,
            coverage = new CoverageSummaryPayload
            {
                exact = coverageSummary.Exact,
                inferred = coverageSummary.Inferred,
                unsupported = coverageSummary.Unsupported,
                ignored = coverageSummary.Ignored,
                potions = result.PotionCount,
                risk = result.Snapshot.HasRisk || !result.Forecast.IsExactForModeledDamage || playerCount > 1,
            },
            win_prob = ProbeWinProbability(result.Snapshot),
            search_status = MapStatus(result),
            // A request may run several adaptive stages; report the whole request, not just the last pass.
            budget_ms = (long)Math.Max(result.Elapsed.TotalMilliseconds, result.TotalSearchElapsed.TotalMilliseconds),
            nodes_expanded = expandedNodes,
            // rollouts_total is reported as the number of expanded search nodes — the beam-search analog
            // of search effort (the engine's turn-boundary rollouts are an internal evaluator detail).
            rollouts_total = expandedNodes,
            confidence = ProbeConfidence(result),
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

    // The engine is a heuristic beam search, not a Monte-Carlo P(victory) estimator. The values below
    // are derived, defensible proxies (documented per field) rather than fabricated guarantees.
    private static double ProbeConfidence(SolverResult result)
    {
        bool unsupported = result.Forecast.HasUnsupportedIntent
            || result.Snapshot.PredictionGaps.Any(gap => !gap.Compensated);
        if (unsupported)
            return 0.3;
        return result.Forecast.IsExactForModeledDamage ? 0.9 : 0.7;
    }

    private static double ProbeWinProbability(SolverSnapshot snapshot)
    {
        if (snapshot.AllEnemiesDead)
            return 1.0;
        if (snapshot.PlayerDead)
            return 0.0;
        // Crude terminal-state proxy: survival margin of player HP vs remaining enemy HP, centered at 0.5.
        double margin = snapshot.PlayerHp - snapshot.EnemyHp;
        return Math.Clamp(0.5 + Math.Tanh(margin / 40.0) * 0.5, 0.0, 1.0);
    }

    private static SolverLineStep MapStep(PlanAction a, Player me)
    {
        string kind = a.Kind == PlanActionKind.PlayCard ? "play_card"
            : a.Kind == PlanActionKind.UsePotion ? "use_potion"
            : "end_turn";
        int? cardIndex = a.Turn == me.PlayerCombatState?.TurnNumber ? MapStepCardIndex(a, me) : null;
        var choice = MapChoice(a);   // 任意计划卡牌选择（消耗/拉弃牌堆/生成到手上/变换/弃牌等）
        return new SolverLineStep
        {
            kind = kind,
            card_index = cardIndex,
            card_id = a.Kind == PlanActionKind.UsePotion ? a.PotionId : a.CardId,
            card_name = a.Kind == PlanActionKind.UsePotion ? a.PotionTitle : a.CardTitle,
            target_index = a.TargetIndex >= 0 ? a.TargetIndex : null,
            choice_card_id = choice.Item1,
            choice_card_name = choice.Item2,
        };
    }

    /// <summary>任意计划卡牌选择（消耗/拉弃牌堆/生成到手/变换/弃牌等）：取 PlanAction.Choice.Cards[0]。
    /// 无选择返回 (null, null)。</summary>
    private static (string?, string?) MapChoice(PlanAction a)
    {
        if (a.Choice is not { Cards.Count: > 0 })
        {
            return (null, null);
        }
        var token = a.Choice.Cards[0];
        return (token.CardId, token.Title);
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

    /// <summary>
    /// Card-side coverage of the hand this plan's current turn plays from. The four buckets are a genuine
    /// partition produced by the shared classifier in <see cref="SimCoverage"/> (same semantics as
    /// Game/Sim/SimSolverPlan.cs), so:
    /// <list type="bullet">
    /// <item>DENOMINATOR = the LIVE hand captured on the game thread together with the root snapshot
    /// (== the root snapshot's hand, i.e. the hand <see cref="MapStepCardIndex"/> indexes into).
    /// It is <b>not</b> <c>result.Snapshot.HandCount</c>: that is the hand count of the FINAL simulated
    /// node at the end of the searched horizon, a different quantity in a different unit — subtracting
    /// one from the other was the old bug.</item>
    /// <item>NUMERATOR = one bucket per card of that hand ⇒ exact+inferred+unsupported+ignored == hand size
    /// (never a gap-entry count, never a <c>Math.Max(0, …)</c> plug).</item>
    /// <item>EVIDENCE = <c>result.Snapshot.PredictionGaps</c>, but a gap counts only for the card it is
    /// about (<c>SourceId == card id</c> of this hand). PredictionGaps is the deduped risk list of the
    /// whole search and also contains powers / relics / monsters; those are not card-side coverage.</item>
    /// <item>ENEMY ITEMS = <c>Forecast.ApproximationDetails</c> / <c>Forecast.UnsupportedDetails</c> are
    /// enemy-intent forecasts, not cards. They are carried on their own summary fields (surfaced only as
    /// <c>enemy_approximation:</c> / <c>enemy_unsupported:</c> warnings) and never enter these buckets.</item>
    /// </list>
    /// When the hand could not be captured the whole report is honestly unknown: exact = -1 (see
    /// <see cref="SimCoverageSummary.UnknownValue"/>), with a <c>coverage_unknown:</c> warning.
    /// </summary>
    private static SimCoverageSummary SummarizeCoverage(
        SolverResult result, IReadOnlyList<SimCoverageCard>? handCoverage)
    {
        IReadOnlyList<PredictionGap> predictionGaps = result.Snapshot.PredictionGaps;
        var gaps = new SimCoverageGap[predictionGaps.Count];
        for (var i = 0; i < gaps.Length; i++)
        {
            PredictionGap gap = predictionGaps[i];
            gaps[i] = new SimCoverageGap(gap.SourceId, gap.Compensated);
        }

        return SimCoverage.Summarize(
            handCoverage,
            gaps,
            enemyApproximated: result.Forecast.ApproximationDetails.Count,
            enemyUnsupported: result.Forecast.UnsupportedDetails.Count);
    }

    /// <summary>
    /// Snapshot the live hand (game thread) into the pure classifier input: card id + the two X-cost
    /// predicates. Uses exactly the same predicates as Game/Sim/SimBuild.cs:91-92
    /// (<c>card.EnergyCost.CostsX</c> / <c>card.HasStarCostX</c>), so the CombatSolver path and the Sim path
    /// agree on what "ignored" means. Returns null when the hand is not readable (=> coverage unknown).
    /// </summary>
    private static SimCoverageCard[]? CaptureHandCoverage(Player me)
    {
        var playerCombat = me.PlayerCombatState;
        if (playerCombat == null)
            return null;
        var cards = playerCombat.Hand.Cards;
        if (cards == null)
            return null;

        var hand = new SimCoverageCard[cards.Count];
        for (var i = 0; i < hand.Length; i++)
        {
            var card = cards[i];
            hand[i] = new SimCoverageCard(
                card.Id.Entry,
                CostsX: card.EnergyCost.CostsX,
                CostsStarX: card.HasStarCostX,
                // The CombatSolver path has no per-card model flags: only the search's gaps carry
                // evidence about a card, so these stay false (they are the Sim pipeline's own flags).
                Unmodeled: false,
                Approximated: false);
        }
        return hand;
    }

    private static string[] BuildWarnings(
        SolverResult result, SimCoverageSummary coverage, IReadOnlyList<SimCoverageCard>? handCoverage)
    {
        var warnings = new System.Collections.Generic.List<string>();
        if (result.BoundaryReason != SearchBoundaryReason.None)
            warnings.Add($"search_boundary:{result.BoundaryReason.ToString().ToLowerInvariant()}");

        // Same population AND same precedence as the coverage buckets: a gap may only be printed as a
        // card-side line when its source is a card of the reported hand, and its prefix is the bucket that
        // card actually landed in — an X/star-X card is `ignored:` even if the search recorded a risk entry
        // about it (that is why `inferred:`/`unsupported:` can never exceed the hand size). Everything else
        // PredictionGaps carries — deck/discard cards simulated later in the line, powers, relics, monsters —
        // is search-scope evidence and gets its own `search_gap_*` prefix. Before T40 all of it was printed
        // as `inferred:` and then counted against the hand, producing "7 inferred warnings for a 1-card hand".
        var handById = new Dictionary<string, SimCoverageCard>(StringComparer.Ordinal);
        if (handCoverage != null)
            foreach (SimCoverageCard card in handCoverage)
                handById[card.Id] = card;
        foreach (var gap in result.Snapshot.PredictionGaps)
        {
            string prefix;
            if (handById.TryGetValue(gap.SourceId, out SimCoverageCard handCard))
            {
                prefix = handCard.CostsX || handCard.CostsStarX ? "ignored"
                    : gap.Compensated ? "inferred"
                    : "unsupported";
            }
            else
            {
                prefix = gap.Compensated ? "search_gap_inferred" : "search_gap_unsupported";
            }
            warnings.Add($"{prefix}:{gap.SourceId}.{gap.Method}");
        }

        foreach (var detail in result.Forecast.UnsupportedDetails)
            warnings.Add($"enemy_unsupported:{detail}");
        foreach (var detail in result.Forecast.ApproximationDetails)
            warnings.Add($"enemy_approximation:{detail}");
        // Self-describing coverage scope, so a consumer never has to guess the denominator again: the
        // four buckets were classified against this hand, and they partition it.
        if (!coverage.IsKnown)
            warnings.Add("coverage_unknown:手牌捕获不可用,coverage 四分类记为 -1(未知)");
        else
            warnings.Add($"coverage_scope:hand={coverage.HandCount}:classified={coverage.Classified}");
        if (coverage.IsKnown && !coverage.IsConsistent)
            warnings.Add($"coverage_inconsistent:hand={coverage.HandCount}:classified={coverage.Classified}");
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
    public static SolverFacadeOptions Default { get; } = new();

    /// <summary>Wall-clock ceiling (seconds) for the engine's adaptive hard-fight deepening. Engine
    /// default 4 s; the engine clamps to 1..15 s.</summary>
    public double MaxThinkSeconds { get; init; } = SearchPolicySnapshot.DefaultMaxHardFightThinkSeconds;

    public int MaxDegreeOfParallelism { get; init; } = SolverWeights.DefaultSearchMaxDegreeOfParallelism;
}
