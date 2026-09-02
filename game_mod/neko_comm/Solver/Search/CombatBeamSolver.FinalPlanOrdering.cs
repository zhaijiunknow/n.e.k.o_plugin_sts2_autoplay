namespace CombatSolver;

internal sealed partial class CombatBeamSolver
{
    private sealed class FinalPlanOrdering(
        SolverPotionPolicy potionPolicy,
        PotionStrategySnapshot potionStrategy,
        bool enforcePotionDirectives,
        bool renewablePotionShapedRock,
        SolverTheftPolicy? theftPolicy,
        BossHpRelief bossHpRelief,
        PotionFreePolicyBaseline? potionFreePolicyBaseline,
        int initialPlayerMaxHp,
        int minimumPotionUses,
        SearchDiagnosticsSink diagnostics,
        bool detailedDiagnostics,
        BattleDamageSnapshot battleDamage)
    {
        /// <summary>
        /// Fifths of a normal fight's HP weight. Clearing acts one and two restores 80% of combat HP loss, while
        /// the run's last fight only needs a surviving route.
        /// </summary>
        private readonly int _hpWeightFifths = bossHpRelief switch
        {
            BossHpRelief.RunEnding => 0,
            BossHpRelief.ActClearHeal => 1,
            _ => 5,
        };

        /// <summary>
        /// The HP a potion must save to be worth spending, scaled by how much HP is worth in this fight. When HP
        /// buys nothing, no amount of saved HP justifies a potion and only the win/lose escape in
        /// <see cref="PotionUsePolicy.IsEligible"/> can still admit one.
        /// </summary>
        private int ScalePotionCost(int strategicHpCost)
            => PotionUsePolicy.SmartRequiredHpSaved(strategicHpCost, bossHpRelief);

        public FinalPlanSelection Select(
            IReadOnlyList<(SearchNode Node, SimulationSnapshot Snapshot)> evaluated,
            int initialHp,
            bool emitDiagnostics)
        {
            var policyCandidates = evaluated
                .Select(candidate =>
                {
                    SearchFeatures features = SearchFeatures.Capture(candidate.Node);
                    int sold = features.FutureSoldHp;
                    int battleSold = battleDamage.SoldHpCommitted + sold;
                    int potionCount = features.PotionCount;
                    int explicitPotionCount = PotionUsePolicy.ExplicitUseCount(
                        potionCount,
                        candidate.Snapshot.AutomaticPotionUseCount);
                    int? combatEndedTurn = features.AllEnemiesDead
                        && features.BoundaryReason != SearchBoundaryReason.UnsupportedEffect
                            ? candidate.Node.Action?.Turn
                            : null;
                    int ambergrisCount = candidate.Node.Actions.Count(action =>
                        action.Kind == PlanActionKind.UsePotion
                        && string.Equals(action.PotionId, "AMBERGRIS", StringComparison.Ordinal));
                    ForcedPotionUseEvaluation forced = enforcePotionDirectives
                        ? potionStrategy.EvaluateForcedUses(
                            candidate.Node.Actions,
                            renewablePotionShapedRock)
                        : new ForcedPotionUseEvaluation(true, 0, 0, 0);
                    int explicitPotionStrategicCost = candidate.Node.Actions
                        .Where(action => action.Kind == PlanActionKind.UsePotion)
                        .Sum(action => PotionUsePolicy.StrategicHpCost(
                            action.PotionId!,
                            renewablePotionShapedRock));
                    int optionalPotionCount = Math.Max(
                        0,
                        explicitPotionCount - forced.ForcedUseCount);
                    int optionalPotionStrategicCost = Math.Max(
                        0,
                        explicitPotionStrategicCost - forced.ForcedStrategicHpCost);
                    int optionalAmbergrisCount = Math.Max(0, ambergrisCount - forced.ForcedAmbergrisCount);
                    SolverPotionPolicy effectivePotionPolicy = potionPolicy switch
                    {
                        SolverPotionPolicy.RequireAtLeastOne when forced.ForcedUseCount > 0
                            => SolverPotionPolicy.Smart,
                        SolverPotionPolicy.Disabled when optionalPotionCount > 0
                            => SolverPotionPolicy.Smart,
                        _ => potionPolicy,
                    };
                    int hpDeficit = features.CumulativePlayerHpLost;
                    int maxHpDeficit = Math.Max(0, initialPlayerMaxHp - features.PlayerMaxHp);
                    int strategicHpDeficit = hpDeficit + maxHpDeficit;
                    int healthResourceCost = initialHp - features.PlayerHp
                        + initialPlayerMaxHp - features.PlayerMaxHp;
                    int strategicSold = battleSold;
                    int policyHpDeficit = strategicHpDeficit
                        + (effectivePotionPolicy == SolverPotionPolicy.RequireAtLeastOne
                            ? PotionUsePolicy.AdditionalRequiredUseStrategicHpCost(
                                optionalPotionStrategicCost)
                            : 0);
                    return (candidate.Node, candidate.Snapshot, Features: features,
                        FutureSold: sold, BattleSold: battleSold, PotionCount: potionCount,
                        CombatEndedTurn: combatEndedTurn,
                        ExplicitPotionCount: explicitPotionCount, HpDeficit: hpDeficit,
                        StrategicHpDeficit: strategicHpDeficit, PolicyHpDeficit: policyHpDeficit,
                        MaxHpDeficit: maxHpDeficit, HealthResourceCost: healthResourceCost,
                        StrategicSold: strategicSold, PotionStrategicCost: candidate.Node.PotionStrategicCost,
                        AmbergrisCount: ambergrisCount, Score: features.Score,
                        ForcedUsesSatisfied: forced.AllForcedUsesSatisfied,
                        OptionalPotionCount: optionalPotionCount,
                        OptionalPotionStrategicCost: optionalPotionStrategicCost,
                        OptionalAmbergrisCount: optionalAmbergrisCount,
                        EffectivePotionPolicy: effectivePotionPolicy);
                })
                .ToList();
            if (emitDiagnostics && detailedDiagnostics)
            {
                foreach (var potionGroup in policyCandidates
                             .GroupBy(candidate => candidate.PotionCount)
                             .OrderBy(group => group.Key))
                {
                    var diagnostic = potionGroup
                        .OrderByDescending(candidate => candidate.Features.AllEnemiesDead)
                        .ThenByDescending(candidate => candidate.Features.ProjectedPlayerHp)
                        .ThenBy(candidate => candidate.Features.EnemyHp)
                        .ThenByDescending(candidate => candidate.Score)
                        .First();
                    diagnostics.Info(
                        $"[CombatSolver/Debug] POTION_FINAL_CANDIDATE count={potionGroup.Key} " +
                        $"won={diagnostic.Features.AllEnemiesDead} hp={diagnostic.Snapshot.PlayerHp} " +
                        $"projected_hp={diagnostic.Features.ProjectedPlayerHp} " +
                        $"enemy_hp={diagnostic.Features.EnemyHp} " +
                        $"actions={string.Join(',', diagnostic.Node.Actions.Select(CombatBeamSolver.PolicyActionToken))}");
                }
            }
            int potionFreeBaselineIndex = policyCandidates
                .Select((candidate, index) => (Candidate: candidate, Index: index))
                .Where(item => item.Candidate.ExplicitPotionCount == 0)
                .OrderByDescending(item => item.Candidate.Features.AllEnemiesDead)
                .ThenBy(item => theftPolicy == SolverTheftPolicy.PreserveResources
                    ? item.Candidate.Features.OutstandingStolenResource
                    : 0)
                .ThenBy(item => item.Candidate.StrategicHpDeficit)
                .ThenBy(item => item.Candidate.HealthResourceCost)
                .ThenByDescending(item => item.Candidate.Features.LongTermResourceValue)
                .ThenBy(item => item.Candidate.Features.AngerCopiesGenerated)
                .ThenBy(item => CombatBeamSolver.PolicyBoundaryRank(item.Candidate.Features.BoundaryReason))
                .ThenBy(item => item.Candidate.Features.EnemyHp)
                .ThenBy(item => item.Candidate.CombatEndedTurn ?? int.MaxValue)
                .ThenByDescending(item => item.Candidate.Score)
                .ThenBy(item => item.Candidate.StrategicSold)
                .ThenBy(item => item.Candidate.Features.ActionCount)
                .Select(item => item.Index)
                .DefaultIfEmpty(-1)
                .First();
            bool hasPotionFreeBaseline = potionFreeBaselineIndex >= 0;
            bool potionFreeWon = hasPotionFreeBaseline
                && policyCandidates[potionFreeBaselineIndex].Features.AllEnemiesDead;
            int potionFreeStrategicHpDeficit = hasPotionFreeBaseline
                ? policyCandidates[potionFreeBaselineIndex].StrategicHpDeficit
                : initialHp;
            int potionFreePlayerHp = hasPotionFreeBaseline
                ? policyCandidates[potionFreeBaselineIndex].Snapshot.PlayerHp
                : 0;
            int potionFreeOutstandingResource = hasPotionFreeBaseline
                ? policyCandidates[potionFreeBaselineIndex].Features.OutstandingStolenResource
                : int.MaxValue;
            if (potionFreePolicyBaseline is { } auditedBaseline)
            {
                hasPotionFreeBaseline = true;
                potionFreeWon = auditedBaseline.Won;
                potionFreeStrategicHpDeficit = auditedBaseline.HpDeficit;
                potionFreePlayerHp = auditedBaseline.PlayerHp;
            }
            bool anyRouteWon = potionFreeWon
                || policyCandidates.Any(candidate => candidate.Features.AllEnemiesDead);
            if (emitDiagnostics)
            {
                if (potionFreeBaselineIndex >= 0)
                {
                    var potionFreeBaseline = policyCandidates[potionFreeBaselineIndex];
                    diagnostics.Info(
                        $"[CombatSolver/Test] POLICY_BASELINE kind=potion_free " +
                        $"won={potionFreeWon} hp_deficit={potionFreeBaseline.HpDeficit} " +
                        $"enemy_hp={potionFreeBaseline.Features.EnemyHp} " +
                        $"boundary={potionFreeBaseline.Features.BoundaryReason} " +
                        $"actions={string.Join(',', potionFreeBaseline.Node.Actions.Select(CombatBeamSolver.PolicyActionToken))}");
                }
                else
                {
                    diagnostics.Info(
                        $"[CombatSolver/Test] POLICY_BASELINE kind=potion_free missing=true " +
                        $"won=false hp_deficit={initialHp}");
                }
                if (potionFreePolicyBaseline is { } baselineOverride)
                {
                    diagnostics.Info(
                        $"[CombatSolver/Test] POLICY_BASELINE_OVERRIDE kind=potion_free " +
                        $"won={baselineOverride.Won} hp_deficit={baselineOverride.HpDeficit}");
                }
            }
            var selected = policyCandidates
                .Where(candidate =>
                    candidate.ForcedUsesSatisfied
                    && candidate.ExplicitPotionCount >= minimumPotionUses
                    && (PotionUsePolicy.IsEligible(
                         candidate.EffectivePotionPolicy,
                         candidate.OptionalPotionCount,
                         ScalePotionCost(candidate.OptionalPotionStrategicCost),
                         potionFreeWon,
                         potionFreeStrategicHpDeficit,
                         anyRouteWon,
                         candidate.Features.AllEnemiesDead,
                         candidate.StrategicHpDeficit)
                     || theftPolicy == SolverTheftPolicy.PreserveResources
                        && candidate.PotionCount > 0
                        && candidate.Features.OutstandingStolenResource < potionFreeOutstandingResource)
                    && PotionUsePolicy.MeetsAmbergrisRestriction(
                        hasPotionFreeBaseline,
                        candidate.OptionalAmbergrisCount,
                        candidate.OptionalPotionStrategicCost,
                        initialPlayerMaxHp,
                        potionFreePlayerHp,
                        candidate.Snapshot.PlayerHp))
                .OrderByDescending(candidate => candidate.Features.AllEnemiesDead)
                // Survival used to be implied by the HP deficit being maximal on a death route. Once HP can be
                // weighted down to nothing it has to be stated, or a run-ending boss would rank a lethal route.
                .ThenBy(candidate => candidate.Snapshot.PlayerDead
                    || candidate.Snapshot.ProjectedPlayerHp <= 0
                        ? 1
                        : 0)
                .ThenBy(candidate => theftPolicy == SolverTheftPolicy.PreserveResources
                    ? candidate.Features.OutstandingStolenResource
                    : 0)
                .ThenBy(candidate => candidate.PolicyHpDeficit * _hpWeightFifths)
                .ThenBy(candidate => candidate.HealthResourceCost * _hpWeightFifths)
                .ThenByDescending(candidate => candidate.Features.LongTermResourceValue)
                .ThenBy(candidate => candidate.Features.AngerCopiesGenerated)
                .ThenBy(candidate => CombatBeamSolver.PolicyBoundaryRank(candidate.Features.BoundaryReason))
                .ThenBy(candidate => candidate.OptionalPotionCount)
                .ThenBy(candidate => candidate.StrategicSold)
                .ThenBy(candidate => candidate.Features.EnemyHp)
                .ThenBy(candidate => candidate.CombatEndedTurn ?? int.MaxValue)
                .ThenByDescending(candidate => candidate.Score)
                .ThenBy(candidate => candidate.Features.ActionCount)
                .ToList();
            if (selected.Count == 0)
            {
                throw new PotionPolicyUnsatisfiedException(
                    enforcePotionDirectives && potionStrategy.HasForcedDirectives
                        ? $"指定药水必须使用，但搜索没有找到可执行路线：{potionStrategy.DescribeForcedUses()}。"
                        : potionPolicy == SolverPotionPolicy.RequireAtLeastOne
                        ? "本场药水策略要求至少使用一瓶，但搜索没有找到可执行的用药路线。"
                        : "本场药水策略没有可执行路线。");
            }
            var selectedCandidate = selected[0];
            int potionBranchesRejected = policyCandidates.Count(candidate =>
                candidate.PotionCount > 0
                && (!candidate.ForcedUsesSatisfied
                    || candidate.ExplicitPotionCount < minimumPotionUses
                    || !(PotionUsePolicy.IsEligible(
                          candidate.EffectivePotionPolicy,
                          candidate.OptionalPotionCount,
                          ScalePotionCost(candidate.OptionalPotionStrategicCost),
                          potionFreeWon,
                          potionFreeStrategicHpDeficit,
                          anyRouteWon,
                          candidate.Features.AllEnemiesDead,
                          candidate.StrategicHpDeficit)
                      || theftPolicy == SolverTheftPolicy.PreserveResources
                         && candidate.Features.OutstandingStolenResource < potionFreeOutstandingResource)
                    || !PotionUsePolicy.MeetsAmbergrisRestriction(
                        hasPotionFreeBaseline,
                        candidate.OptionalAmbergrisCount,
                        candidate.OptionalPotionStrategicCost,
                        initialPlayerMaxHp,
                        potionFreePlayerHp,
                        candidate.Snapshot.PlayerHp)));
            int potionHpSaved = selectedCandidate.PotionCount == 0
                ? 0
                : selectedCandidate.AmbergrisCount > 0
                    ? Math.Max(0, selectedCandidate.Snapshot.PlayerHp - potionFreePlayerHp)
                    : PotionUsePolicy.HpSaved(
                        potionFreeStrategicHpDeficit,
                        selectedCandidate.StrategicHpDeficit);
            int potionHpRequired = PotionUsePolicy.EffectiveStrategicHpCost(
                selectedCandidate.OptionalPotionStrategicCost,
                selectedCandidate.OptionalAmbergrisCount,
                initialPlayerMaxHp);
            if (selectedCandidate.EffectivePotionPolicy == SolverPotionPolicy.Smart
                && selectedCandidate.OptionalAmbergrisCount == 0
                && potionFreeWon)
            {
                potionHpRequired = PotionUsePolicy.SmartRequiredHpSaved(
                    potionHpRequired,
                    bossHpRelief);
            }
            if (selectedCandidate.EffectivePotionPolicy == SolverPotionPolicy.RequireAtLeastOne)
            {
                potionHpRequired = PotionUsePolicy.AdditionalRequiredUseStrategicHpCost(
                    potionHpRequired);
            }
            return new FinalPlanSelection(
                new FinalPlanCandidate(
                    selectedCandidate.Node,
                    selectedCandidate.Snapshot,
                    selectedCandidate.Features,
                    selectedCandidate.FutureSold,
                    selectedCandidate.BattleSold,
                    selectedCandidate.PotionCount,
                    selectedCandidate.Score),
                potionBranchesRejected,
                potionHpSaved,
                potionHpRequired);
        }
    }
}
