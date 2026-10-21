using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Powers;
using MegaCrit.Sts2.Core.Hooks;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Events;
using MegaCrit.Sts2.Core.Models.Potions;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine;
using MegaCrit.Sts2.Core.ValueProps;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver;


internal sealed partial class CombatBeamSolver
{
    private IEnumerable<SearchNode> Expand(SearchNode node)
    {
        cancellationToken.ThrowIfCancellationRequested();
        SimulationSnapshot snapshot = node.Snapshot;
        if (!TryAdmitExpansionParent(node, snapshot, parallel: false))
            yield break;
        CombatPredictionSimulator simulator = (CombatPredictionSimulator)snapshot.Simulator;
        SimulatedCombatState simulatedCombat = (SimulatedCombatState)simulator.State.CombatState;
        using ExpansionBatch? cycleExitBatch = node.CycleProbeLease == null
            && node.CycleExitProbe == null
            ? null
            : RentExpansionBatch();
        if (cycleExitBatch != null)
        {
            GenerateRawPotionCandidates(node, cycleExitBatch);
            GenerateRawEndTurnCandidates(node, cycleExitBatch);
        }

        List<ActionCandidate> nonDominated = new(16);
        List<ActionCandidate>? deferredCycleCandidates = null;
        using AdmittedParent cardJobs = new(node);
        AdmittedJobScheduler scheduler = new([cardJobs], degreeOfParallelism: 1, wave: null);
        cardJobs.PrepareSerialCards(this, scheduler.NextJob(0, SerialJobPhase.Prepare)
            ?? throw new InvalidOperationException("串行父节点缺少准备作业。"));
        int processedCards = 0;
        while (scheduler.NextJob(0, SerialJobPhase.Card) is { } cardJob)
        {
            if (cardJob.Kind == ParallelExpansionWorkProfile.Kind.Choice)
                cardJobs.RunSerialChoiceJob(this, cardJob);
            else
                cardJobs.RunSerialCardAction(this, cardJob);
            ExpansionBatch cards = cardJobs.Aggregate!;
            while (processedCards < cards.Cards.Count)
            {
                RawCardCandidate raw = cards.Cards[processedCards++];
                cards.Transfer(raw.Node.Snapshot);
                ProcessExpandedCardCandidate(node, raw,
                    nonDominated, ref deferredCycleCandidates, batch: null);
            }
        }

        if (cycleExitBatch != null)
        {
            PruneCommittedCrossTurnCandidates(cycleExitBatch.Potions, cycleExitBatch);
            PruneCommittedCrossTurnCandidates(cycleExitBatch.EndTurns, cycleExitBatch);
        }
        CommitDeferredCycleCandidates(
            nonDominated,
            deferredCycleCandidates,
            batch: null);
        if (NeedsCycleExitAdmission(node, nonDominated, cycleExitBatch?.Potions, cycleExitBatch?.EndTurns))
        {
            SearchNode[] directChildren = nonDominated.Select(candidate => candidate.Node)
                .Concat(cycleExitBatch?.Potions ?? [])
                .Concat(cycleExitBatch?.EndTurns ?? [])
                .ToArray();
            foreach (SearchNode directChild in directChildren)
                CommitCycleExitObservation(directChild);
            AnnotateCycleExitProgress(node, directChildren);
            _ = MaterializeAdmittedCycleExitObservation(
                directChildren,
                _run.CycleFamilyLedger);
        }
        List<ActionCandidate> queuedCandidates = SelectActionCandidates(node, nonDominated);
        AdmitCycleProbeCandidate(nonDominated, queuedCandidates);
        AdmitCycleExitProbeCandidate(nonDominated, queuedCandidates);
        for (int index = queuedCandidates.Count - 1; index >= 0; index--)
        {
            ActionCandidate candidate = queuedCandidates[index];
            if (!ShouldRejectCycleCandidate(candidate.Node))
                continue;
            queuedCandidates.RemoveAt(index);
            nonDominated.RemoveAll(item => ReferenceEquals(item.Node, candidate.Node));
            candidate.Node.Snapshot.ReleaseSimulator();
        }
        _run.TopQueueActionsDropped += nonDominated.Count - queuedCandidates.Count;
        foreach (ActionCandidate candidate in nonDominated)
        {
            if (!queuedCandidates.Any(retained => ReferenceEquals(retained.Node, candidate.Node)))
            {
                candidate.Node.Snapshot.ReleaseSimulator();
            }
        }
        int yieldedCandidateCount = 0;
        try
        {
            while (yieldedCandidateCount < queuedCandidates.Count)
            {
                SearchNode candidate = queuedCandidates[yieldedCandidateCount].Node;
                yieldedCandidateCount++;
                yield return candidate;
            }
        }
        finally
        {
            for (; yieldedCandidateCount < queuedCandidates.Count; yieldedCandidateCount++)
                queuedCandidates[yieldedCandidateCount].Node.Snapshot.ReleaseSimulator();
        }

        if (cycleExitBatch != null)
        {
            foreach (SearchNode child in cycleExitBatch.Potions)
            {
                EnsureBoundedCycleProbeLease(child);
                if (ShouldRejectCycleCandidate(child)
                    || !TryAcceptTransposition(child))
                {
                    cycleExitBatch.Release(child.Snapshot);
                    continue;
                }
                cycleExitBatch.Transfer(child.Snapshot);
                yield return child;
            }
            foreach (SearchNode child in AdmitPlannedEndTurnChildren(cycleExitBatch))
                yield return child;
            yield break;
        }

        if (_detailedDiagnostics && node.ActionCount == 0)
        {
            policy.Diagnostics.Info(
                $"[CombatSolver/Debug] ROOT_POTION_SLOTS count={root.PotionSlotCount} " +
                $"potions={string.Join(',', Enumerable.Range(0, root.PotionSlotCount).Select(slot =>
                {
                    PotionModel? item = simulatedCombat.GetPotionAtSlot(_player, slot);
                    return $"{slot}:{item?.Id.Entry ?? "-"}:{(item != null && PotionOnUseSupport.CanSearch(item))}";
                }))}");
        }
        cardJobs.PrepareSerialPotions(this);
        while (scheduler.NextJob(0, SerialJobPhase.Potion) is { } potionJob)
            foreach (SearchNode child in cardJobs.RunSerialPotionJob(this, potionJob))
                yield return child;

        AdmittedExpansionJob tailJob = scheduler.NextJob(0, SerialJobPhase.Tail)
            ?? throw new InvalidOperationException("串行父节点缺少回合尾部作业。");
        foreach (SearchNode endNode in cardJobs.RunSerialEndTurnJob(this, tailJob))
            yield return endNode;
    }

    private IEnumerable<SearchNode> EnumerateSerialPotionChildren(
        SearchNode node, PreparedPotionAction planned)
    {
        PotionModel potion = planned.Potion;
        using PreparedPotionChoiceWork work = PreparePotionChoiceWork(node, planned);
        if (_detailedDiagnostics && node.ActionCount == 0)
        {
            policy.Diagnostics.Info(
                $"[CombatSolver/Debug] ROOT_POTION_OPTIONS potion={potion.Id.Entry} " +
                $"choices={string.Join(';', work.Choices.Select(choice => choice == null
                    ? "-"
                    : choice.Cards.Count == 0
                        ? "skip"
                        : string.Join(',', choice.Cards.Select(card => card.CardId))))}");
        }
        foreach ((PlanAction finalAction, SimulationSnapshot finalSnapshot) in
                 work.Resolve(this, node, planned.Action))
        {
            SearchNode child = CreatePlannedPotionChild(node, finalAction, finalSnapshot);
            PromoteOrderedMutationProgressTail(child);
            CommitCycleExitObservation(child);
            bool accepted = TryAdmitPlannedPotionChild(child, out bool rejectedByCycle);
            if (rejectedByCycle)
            {
                finalSnapshot.ReleaseSimulator();
                continue;
            }
            if (_detailedDiagnostics && node.ActionCount == 0)
            {
                PlanCardChoice? resolvedChoice = finalAction.Choice;
                policy.Diagnostics.Info(
                    $"[CombatSolver/Debug] ROOT_POTION_BRANCH potion={potion.Id.Entry} " +
                    $"choice={(resolvedChoice == null ? "-" : string.Join(',', resolvedChoice.Cards.Select(card => card.CardId)))} " +
                    $"accepted={accepted} hp={finalSnapshot.PlayerHp} " +
                    $"projected_hp={finalSnapshot.ProjectedPlayerHp} " +
                    $"enemy_hp={finalSnapshot.EnemyHp} hand={finalSnapshot.HandCount} " +
                    $"score={child.Score:0}");
            }
            if (accepted)
                yield return child;
            else
                finalSnapshot.ReleaseSimulator();
        }
    }

    private bool ShouldPruneCrossTurnNoProgress(SearchNode node)
    {
        if (node.Parent == null
            || node.Turn <= node.Parent.Turn
            || node.IsTerminal
            || node.BoundaryReason != SearchBoundaryReason.None)
        {
            return false;
        }
        if (node.CycleExitProbe is { RemainingActions: > 0, RemainingTurnTransitions: >= 0 }
            || HasValidPendingCycleExitObservation(node))
            return false;
        // A forced-turn action can be materialized before the direct EndTurn branches from
        // its turn start. Until turn-outcome annotation has the complete branch-aware baseline,
        // absence of scalar progress is not sufficient evidence for pruning.
        if (!node.CrossTurnSemanticEvidenceAttached)
            return false;

        CombatPredictionSimulator simulator = (CombatPredictionSimulator)node.Snapshot.Simulator;
        SimulatedCombatState combat = (SimulatedCombatState)simulator.State.CombatState;
        int drawPerTurn = Math.Max(
            1,
            PersistentPowerSupport.GetModifiedHandDraw(
                combat,
                _player,
                CombatManager.baseHandDrawCount));
        int deckCycleTurns = Math.Max(
            1,
            (node.Snapshot.LiveDeckSize + drawPerTurn - 1) / drawPerTurn);
        int noProgressLimit = Math.Max(
            SolverWeights.SetupValueHorizonTurns,
            deckCycleTurns * 2);
        if (node.CrossTurnProbe is { } probe)
        {
            int boundedProbeTurns = Math.Clamp(
                checked(noProgressLimit + 1),
                SolverWeights.SetupValueHorizonTurns + 1,
                SolverWeights.SetupValueHorizonTurns * 4);
            if (probe.LastTurnImproved)
                boundedProbeTurns = checked(boundedProbeTurns * 2);
            if (probe.LastTurnChangedSemanticState)
            {
                // Repeated divergence from the exact stand-pat outcome is generic evidence
                // of hidden state movement (mutable counters, powers, RNG, etc.). Give that
                // one tiny portfolio lane a longer, still-hard-bounded horizon.
                boundedProbeTurns = SolverWeights.SetupValueHorizonTurns * 4;
            }
            if (probe.CompletedTurnTransitions > boundedProbeTurns)
            {
                _run.CrossTurnContinuationsStopped++;
                return true;
            }
        }
        if (node.CombatProgress.TurnsWithoutProgress < noProgressLimit)
            return false;
        if (node.CrossTurnProbe != null)
            return false;
        if (_planCommitment is { } plan
            && node.Turn > plan.OpenedTurn
            && PlanHorizonPolicy.ShouldExtend(
                node.CombatProgress.TurnsWithoutProgress,
                noProgressLimit,
                deckCycleTurns,
                plan.CountRealizedPayoffs(node) > 0))
            return false;
        return true;
    }

    private IEnumerable<(PlanAction Action, SimulationSnapshot Snapshot)> BuildEndTurnBranches(
        SearchNode node,
        IReadOnlyList<PlanCardChoice> choices)
    {
        PlanAction action = new(
            PlanActionKind.EndTurn,
            node.Turn,
            TurnStartChoices: choices.Count == 0 ? null : choices);
        SimulationSnapshot snapshot = ReplayAction(node, action);
        foreach ((PlanAction resolvedAction, SimulationSnapshot resolvedSnapshot) in
                 ResolveRoundChoiceBranches(node, action, snapshot))
        {
            yield return (resolvedAction, resolvedSnapshot);
        }
    }

    private IEnumerable<SearchNode> BuildAcceptedEndTurnNodes(SearchNode node)
    {
        using ExpansionBatch batch = RentExpansionBatch();
        GenerateRawEndTurnCandidates(node, batch);
        PruneCommittedCrossTurnCandidates(batch.EndTurns, batch);
        if (NeedsCycleExitAdmission(node, [], null, batch.EndTurns))
        {
            AnnotateCycleExitProgress(node, batch.EndTurns);
            _ = MaterializeAdmittedCycleExitObservation(
                batch.EndTurns,
                _run.CycleFamilyLedger);
        }
        foreach (SearchNode endNode in AdmitPlannedEndTurnChildren(batch))
            yield return endNode;
    }

}
