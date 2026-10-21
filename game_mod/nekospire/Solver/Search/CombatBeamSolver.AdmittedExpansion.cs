using System.Diagnostics;
using System.Runtime.ExceptionServices;

namespace CombatSolver;

internal sealed partial class CombatBeamSolver
{
    private PreparedChoiceEvaluation EvaluateDeferredChoices(
        SearchNode parent,
        DeferredCardActionProbe probe,
        object forkGate)
    {
        if (_parallelActionReplayForkGate != null)
            throw new InvalidOperationException("不能嵌套选择作业的 Fork 上下文。");
        ExpansionBatch batch = RentExpansionBatch();
        bool completed = false;
        _parallelActionReplayForkGate = forkGate;
        try
        {
            cancellationToken.ThrowIfCancellationRequested();
            PrimaryChoiceReplayFrontier? frontier = ResolveDeferredRoundChoiceAction(parent, probe, batch);
            if (frontier != null)
                batch.Dispose();
            completed = true;
            return new PreparedChoiceEvaluation(frontier == null ? batch : null, frontier);
        }
        finally
        {
            _parallelActionReplayForkGate = null;
            if (!completed)
                batch.Dispose();
        }
    }

    private ExpansionBatch EvaluateSerialDeferredChoices(
        SearchNode parent, DeferredCardActionProbe probe)
    {
        ExpansionBatch batch = RentExpansionBatch();
        bool completed = false;
        SimulationSnapshot? snapshot = probe.TakeSnapshot();
        CardChoiceReplayCheckpoint? checkpoint = probe.TakeCheckpoint();
        try
        {
            if (!TryResolvePlannedCardChoices(parent, probe.Action, snapshot,
                    out IEnumerable<(PlanAction Action, SimulationSnapshot Snapshot)> branches))
            {
                snapshot = null; // The choice resolver released the rejected probe.
                completed = true;
                return batch;
            }
            IEnumerable<(PlanAction Action, SimulationSnapshot Snapshot)> resolved =
                WithCardChoiceCheckpoint(checkpoint, branches);
            snapshot = null; // The branch iterator owns the probe once enumeration begins.
            checkpoint = null;
            AddResolvedCardCandidates(parent, probe.Action, resolved, batch);
            completed = true;
            return batch;
        }
        finally
        {
            snapshot?.ReleaseSimulator();
            checkpoint?.Dispose();
            if (!completed)
                batch.Dispose();
        }
    }

    private PreparedChoiceEvaluation EvaluatePreparedPotionAction(
        SearchNode parent,
        PreparedPotionAction action,
        object forkGate)
    {
        if (_parallelActionReplayForkGate != null)
            throw new InvalidOperationException("不能嵌套药水作业的 Fork 上下文。");
        ExpansionBatch batch = RentExpansionBatch();
        bool completed = false;
        _parallelActionReplayForkGate = forkGate;
        try
        {
            cancellationToken.ThrowIfCancellationRequested();
            PrimaryChoiceReplayFrontier? frontier = GeneratePreparedPotionAction(
                parent, action, batch, allowPrimaryReplays: true);
            if (frontier != null)
                batch.Dispose();
            completed = true;
            return new PreparedChoiceEvaluation(frontier == null ? batch : null, frontier);
        }
        finally
        {
            _parallelActionReplayForkGate = null;
            if (!completed)
                batch.Dispose();
        }
    }

    private sealed partial class ParallelExpansionExecutor
    {
        internal int _activeChoiceWorkers;
        internal int _maximumActiveChoiceWorkers;
        internal int _activePrimaryReplayWorkers;

        // The outer wave has already reserved every parent. Jobs cannot admit another parent,
        // change its choice budget, or establish a GC checkpoint while any lane is active.
        private ExpansionWorkerOutcome[] EvaluateQueuedParents(
            IReadOnlyList<SearchNode> nodes,
            Action<int, ExpansionBatch> commitOrdered)
        {
            ExpansionLane[] lanes = EnsureBackgroundLanes();
            using AdmittedJobWave wave = new(DegreeOfParallelism);
            AdmittedParent?[] parents = new AdmittedParent?[nodes.Count];
            AdmittedJobScheduler scheduler = new(parents, DegreeOfParallelism, wave);
            ExpansionWorkerOutcome[] outcomes = new ExpansionWorkerOutcome[nodes.Count];
            Stack<int> idleLanes = new(DegreeOfParallelism);
            for (int index = DegreeOfParallelism - 1; index >= 0; index--)
                idleLanes.Push(index);
            int active = 0;
            int committed = 0;
            int actionsDispatched = 0;
            int choicesDispatched = 0;
            long waitTicks = 0;
            ExceptionDispatchInfo? firstError = null;

            void DispatchAvailable()
            {
                while (firstError == null && idleLanes.TryPeek(out int laneIndex))
                {
                    _coordinator.SearchCancellationToken.ThrowIfCancellationRequested();
                    AdmittedExpansionJob? job = scheduler.NextJob(laneIndex);
                    if (job == null)
                        return;
                    bool registered = false;
                    try
                    {
                        // Publish ownership/state before waking a lane: a continuation may take
                        // its first cached snapshot immediately after the event is signalled.
                        job.Parent.MarkDispatched(job);
                        wave.BackgroundCompleted.AddCount();
                        registered = true;
                        lanes[laneIndex].Dispatch(job);
                    }
                    catch
                    {
                        job.Probe?.Dispose();
                        if (registered)
                            wave.BackgroundCompleted.Signal();
                        throw;
                    }
                    idleLanes.Pop();
                    active++;
                    if (job.Kind == ParallelExpansionWorkProfile.Kind.Action)
                        actionsDispatched++;
                    else if (job.Kind is ParallelExpansionWorkProfile.Kind.Choice
                        or ParallelExpansionWorkProfile.Kind.PrimaryReplay)
                        choicesDispatched++;
                }
            }

            void CommitReadyParents()
            {
                while (committed < parents.Length && parents[committed]!.TailCompleted)
                {
                    AdmittedParent parent = parents[committed]!;
                    ExpansionBatch batch = parent.Aggregate
                        ?? throw new InvalidOperationException("已完成的父作业没有候选批次。");
                    outcomes[committed] = new ExpansionWorkerOutcome(
                        null, batch, null, parent.AllocatedBytes, parent.ElapsedTicks);
                    commitOrdered(committed, batch);
                    parent.Aggregate = null;
                    committed++;
                }
            }

            try
            {
                for (int index = 0; index < nodes.Count; index++)
                {
                    parents[index] = new AdmittedParent(nodes[index]);
                    parents[index]!.Aggregate = _coordinator.RentExpansionBatch();
                }
                DispatchAvailable();
                while (active > 0)
                {
                    long waitStarted = Stopwatch.GetTimestamp();
                    using AdmittedJobOutcome outcome = wave.WaitForNextOutcome();
                    waitTicks += Stopwatch.GetTimestamp() - waitStarted;
                    active--;
                    idleLanes.Push(outcome.Job.LaneIndex);
                    _workProfile.Record(outcome.Job.Kind, outcome.ElapsedTicks, outcome.ActiveKindConcurrency);
                    // Every completed job owns work, including a failed job and siblings
                    // drained after it. Account it once before deciding whether to accept results.
                    _coordinator.MergeExpansionWorker(outcome.Worker, outcome.AllocatedBytes);
                    firstError ??= outcome.Error;
                    if (firstError != null)
                        continue;
                    // A lane's caches and counters may be reused only after this drain. The
                    // published batch/probe lease has a separate owner and can wait for its turn.
                    AdmittedParent parent = outcome.Job.Parent;
                    parent.Receive(outcome);
                    if (parent.TailCompleted)
                        _workProfile.Record(ParallelExpansionWorkProfile.Kind.Parent, parent.ElapsedTicks);
                    DispatchAvailable();
                    CommitReadyParents();
                }
                firstError?.Throw();
                if (committed != nodes.Count)
                    throw new InvalidOperationException("已准入作业未完整按父节点原序提交。");
            }
            finally
            {
                // This sentinel is released exactly once, after dispatch has stopped. On any
                // failure, drain all lanes before releasing probes, aggregates, or parent roots.
                wave.BackgroundCompleted.Signal();
                wave.BackgroundCompleted.Wait();
                _workProfile.Record(ParallelExpansionWorkProfile.Kind.Wait, waitTicks);
                try
                {
                    while (wave.TryTake(out AdmittedJobOutcome? pending))
                    {
                        using (pending)
                        {
                            _coordinator.MergeExpansionWorker(pending!.Worker, pending.AllocatedBytes);
                        }
                    }
                }
                finally
                {
                    foreach (AdmittedParent? parent in parents)
                        parent?.Dispose();
                }
            }

            _coordinator._run.ParallelExpansionWaves++;
            _coordinator._run.ParallelExpansionWorkItems += nodes.Count;
            _coordinator._run.MaxParallelExpansionConcurrency = Math.Max(
                _coordinator._run.MaxParallelExpansionConcurrency,
                Volatile.Read(ref _maximumActiveWorkers));
            if (actionsDispatched > 0)
            {
                _coordinator._run.ParallelActionReplayWaves++;
                _coordinator._run.ParallelActionReplayWorkItems += actionsDispatched;
                _coordinator._run.MaxParallelActionReplayConcurrency = Math.Max(
                    _coordinator._run.MaxParallelActionReplayConcurrency,
                    Volatile.Read(ref _maximumActiveActionReplayWorkers));
            }
            if (choicesDispatched > 0)
            {
                _coordinator._run.ParallelRoundChoiceReplayWaves++;
                _coordinator._run.ParallelRoundChoiceReplayWorkItems += choicesDispatched;
                _coordinator._run.MaxParallelRoundChoiceReplayConcurrency = Math.Max(
                    _coordinator._run.MaxParallelRoundChoiceReplayConcurrency,
                    Volatile.Read(ref _maximumActiveChoiceWorkers));
            }
            return outcomes;
        }
    }

    private enum SerialJobPhase { Prepare, Card, Potion, Tail }

    private sealed class AdmittedJobScheduler(
        AdmittedParent?[] parents, int degreeOfParallelism, AdmittedJobWave? wave)
    {
        private int _cursor;

        public AdmittedExpansionJob? NextJob(int laneIndex, SerialJobPhase? serialPhase = null)
        {
            // Completed choices take priority; the fixed parent window rotates independently
            // of which lane receives the next work item.
            for (int pass = 0; pass < 2; pass++)
            {
                for (int offset = 0; offset < parents.Length; offset++)
                {
                    int index = (_cursor + offset) % parents.Length;
                    AdmittedParent parent = parents[index]!;
                    ChoiceJob? choice = pass == 0 ? parent.FindChoiceJob() : null;
                    ParallelExpansionWorkProfile.Kind? kind = choice != null
                        ? choice.Value.Kind
                        : pass == 0 ? null : parent.NextKind;
                    if (kind == null || serialPhase is { } phase && !Allows(phase, kind.Value))
                        continue;
                    int actionIndex = kind == ParallelExpansionWorkProfile.Kind.Potion
                        ? parent.NextPotion : choice?.ItemIndex ?? parent.NextAction;
                    PreparedCardAction? action = kind == ParallelExpansionWorkProfile.Kind.Action
                        ? parent.Actions![actionIndex] : null;
                    PreparedPotionAction? potion = kind == ParallelExpansionWorkProfile.Kind.Potion
                        ? parent.Potions![actionIndex] : null;
                    DeferredCardActionProbe? probe = choice is { Frontier: null }
                        ? parent.Probes![actionIndex] : null;
                    _cursor = (index + 1) % parents.Length;
                    int replayCount = kind == ParallelExpansionWorkProfile.Kind.PrimaryReplay
                        ? Math.Min(Math.Clamp(choice!.Value.Frontier!.Actions.Length / degreeOfParallelism, 1, 4),
                            choice.Value.Frontier.Actions.Length - choice.Value.ReplayIndex)
                        : 0;
                    return new AdmittedExpansionJob(
                        parent, kind.Value, actionIndex, action, potion, probe, wave, laneIndex,
                        choice?.Frontier, choice?.ReplayIndex ?? -1, replayCount);
                }
            }
            return null;
        }

        private static bool Allows(SerialJobPhase phase, ParallelExpansionWorkProfile.Kind kind)
            => phase switch
            {
                SerialJobPhase.Prepare => kind == ParallelExpansionWorkProfile.Kind.Prepare,
                SerialJobPhase.Card => kind is ParallelExpansionWorkProfile.Kind.Action
                    or ParallelExpansionWorkProfile.Kind.Choice,
                SerialJobPhase.Potion => kind == ParallelExpansionWorkProfile.Kind.Potion,
                SerialJobPhase.Tail => kind == ParallelExpansionWorkProfile.Kind.Tail,
                _ => throw new InvalidOperationException("非法的串行作业阶段。"),
            };
    }

    private readonly record struct ChoiceJob(
            ParallelExpansionWorkProfile.Kind Kind,
            int ItemIndex,
            PrimaryChoiceReplayFrontier? Frontier,
            int ReplayIndex);

    private sealed class AdmittedParent(SearchNode node) : IDisposable
        {
            public SearchNode Node { get; } = node;
            public object ForkGate { get; } = new();
            public ExpansionBatch? Aggregate;
            public List<PreparedCardAction>? Actions;
            public List<PreparedPotionAction>? Potions;
            public DeferredCardActionProbe?[]? Probes;
            private ExpansionBatch?[]? _actionBatches;
            private bool[]? _actionCompleted;
            private ExpansionBatch?[]? _potionBatches;
            private bool[]? _potionCompleted;
            private PrimaryChoiceReplayFrontier?[]? _cardFrontiers;
            private PrimaryChoiceReplayFrontier?[]? _potionFrontiers;
            private PrimaryChoiceReplayFrontier? _endTurnFrontier;
            private bool _prepareDispatched;
            private bool _tailDispatched;
            private ExpansionBatch? _endTurnBatch;
            private bool _endTurnCompleted;
            private IReadOnlyList<CrossTurnStandPatBaseline>? _endTurnBaselines;
            private int _completedActions;
            private int _completedPotions;
            private int _nextAppend;
            private int _nextPotionAppend;
            private long _startedAt;
            public int NextAction;
            public int NextPotion;
            public bool TailCompleted;
            public long AllocatedBytes;
            public long ElapsedTicks;

            public ParallelExpansionWorkProfile.Kind? NextKind => !_prepareDispatched
                ? ParallelExpansionWorkProfile.Kind.Prepare
                : Actions == null ? null
                : NextPotion < Potions!.Count ? ParallelExpansionWorkProfile.Kind.Potion
                : NextAction < Actions.Count ? ParallelExpansionWorkProfile.Kind.Action
                : !_tailDispatched ? ParallelExpansionWorkProfile.Kind.Tail : null;

            public void PrepareSerialCards(CombatBeamSolver solver, AdmittedExpansionJob job)
            {
                if (Aggregate != null || _prepareDispatched
                    || !ReferenceEquals(job.Parent, this)
                    || job.Kind != ParallelExpansionWorkProfile.Kind.Prepare)
                    throw new InvalidOperationException("串行父节点重复准备作业。");
                Aggregate = solver.RentExpansionBatch();
                MarkDispatched(job);
                using var outcome = new AdmittedJobOutcome(job, solver)
                {
                    Actions = solver.PrepareCardActions(Node, cardNameFirst: false),
                    Potions = [],
                };
                Receive(outcome);
            }

            public void RunSerialCardAction(CombatBeamSolver solver, AdmittedExpansionJob job)
            {
                if (FindChoiceJob() != null
                    || NextKind != ParallelExpansionWorkProfile.Kind.Action
                    || !ReferenceEquals(job.Parent, this)
                    || job.Kind != ParallelExpansionWorkProfile.Kind.Action
                    || job.ItemIndex != NextAction)
                    throw new InvalidOperationException("串行卡牌作业次序错误。");
                int index = NextAction;
                MarkDispatched(job);
                PreparedCardActionEvaluation evaluation = solver.EvaluatePreparedCardAction(
                    Node, Actions![index], seed: null, ForkGate);
                using var outcome = new AdmittedJobOutcome(job, solver)
                {
                    Batch = evaluation.Batch,
                    Probe = evaluation.DeferredProbe,
                };
                Receive(outcome);
            }

            public void RunSerialChoiceJob(CombatBeamSolver solver, AdmittedExpansionJob job)
            {
                ChoiceJob choice = FindChoiceJob()
                    ?? throw new InvalidOperationException("串行选择作业缺少挂起选择。");
                if (choice.Kind != ParallelExpansionWorkProfile.Kind.Choice
                    || choice.Frontier != null
                    || !ReferenceEquals(job.Parent, this)
                    || job.Kind != choice.Kind || job.ItemIndex != choice.ItemIndex)
                    throw new InvalidOperationException("串行选择作业意外进入并行回放 frontier。");
                DeferredCardActionProbe probe = Probes![choice.ItemIndex]
                    ?? throw new InvalidOperationException("串行选择探针已被消费。");
                MarkDispatched(job);
                try
                {
                    using var outcome = new AdmittedJobOutcome(job, solver)
                    {
                        Batch = solver.EvaluateSerialDeferredChoices(Node, probe),
                    };
                    Receive(outcome);
                }
                finally
                {
                    probe.Dispose();
                }
            }

            public void PrepareSerialPotions(CombatBeamSolver solver)
            {
                if (Actions == null || _completedActions != Actions.Count
                    || FindChoiceJob() != null || Potions is not { Count: 0 }
                    || NextPotion != 0)
                    throw new InvalidOperationException("串行药水作业只能在卡牌选择全部完成后准备。");
                Potions = solver.PreparePotionActions(Node);
                _potionBatches = new ExpansionBatch?[Potions.Count];
                _potionCompleted = new bool[Potions.Count];
                _potionFrontiers = new PrimaryChoiceReplayFrontier?[Potions.Count];
            }

            public IEnumerable<SearchNode> RunSerialPotionJob(
                CombatBeamSolver solver, AdmittedExpansionJob job)
            {
                if (NextKind != ParallelExpansionWorkProfile.Kind.Potion
                    || FindChoiceJob() != null
                    || !ReferenceEquals(job.Parent, this)
                    || job.Kind != ParallelExpansionWorkProfile.Kind.Potion
                    || job.ItemIndex != NextPotion)
                    throw new InvalidOperationException("串行药水作业次序错误。");
                int index = NextPotion;
                MarkDispatched(job);
                bool completed = false;
                try
                {
                    foreach (SearchNode child in solver.EnumerateSerialPotionChildren(
                                 Node, Potions![index]))
                        yield return child;
                    completed = true;
                }
                finally
                {
                    if (completed)
                    {
                        using var outcome = new AdmittedJobOutcome(job, solver)
                        {
                            SerialCompletionOnly = true,
                        };
                        Receive(outcome);
                    }
                }
            }

            public IEnumerable<SearchNode> RunSerialEndTurnJob(
                CombatBeamSolver solver, AdmittedExpansionJob job)
            {
                if (FindChoiceJob() != null
                    || NextKind != ParallelExpansionWorkProfile.Kind.Tail
                    || !ReferenceEquals(job.Parent, this)
                    || job.Kind != ParallelExpansionWorkProfile.Kind.Tail)
                    throw new InvalidOperationException("串行回合尾部作业次序错误。");
                MarkDispatched(job);
                bool completed = false;
                try
                {
                    foreach (SearchNode child in solver.BuildAcceptedEndTurnNodes(Node))
                        yield return child;
                    completed = true;
                }
                finally
                {
                    if (completed)
                    {
                        using var outcome = new AdmittedJobOutcome(job, solver)
                        {
                            SerialCompletionOnly = true,
                        };
                        Receive(outcome);
                    }
                }
            }

            public ChoiceJob? FindChoiceJob()
            {
                for (int family = 0; family < 2; family++)
                {
                    PrimaryChoiceReplayFrontier?[]? frontiers = family == 0 ? _cardFrontiers : _potionFrontiers;
                    if (frontiers == null)
                        continue;
                    for (int index = 0; index < frontiers.Length; index++)
                    {
                        PrimaryChoiceReplayFrontier? frontier = frontiers[index];
                        if (frontier?.CanDispatchReplay == true)
                            return new ChoiceJob(ParallelExpansionWorkProfile.Kind.PrimaryReplay,
                                index, frontier, frontier.NextReplay);
                        if (frontier?.CanDispatchContinuation == true)
                            return new ChoiceJob(ParallelExpansionWorkProfile.Kind.Choice,
                                index, frontier, ReplayIndex: -1);
                    }
                }
                if (_endTurnFrontier?.CanDispatchReplay == true)
                    return new ChoiceJob(ParallelExpansionWorkProfile.Kind.PrimaryReplay,
                        -1, _endTurnFrontier, _endTurnFrontier.NextReplay);
                if (_endTurnFrontier?.CanDispatchContinuation == true)
                    return new ChoiceJob(ParallelExpansionWorkProfile.Kind.Choice,
                        -1, _endTurnFrontier, ReplayIndex: -1);
                if (Probes != null)
                {
                    for (int index = 0; index < Probes.Length; index++)
                        if (Probes[index] != null)
                            return new ChoiceJob(ParallelExpansionWorkProfile.Kind.Choice,
                                index, Frontier: null, ReplayIndex: -1);
                }
                return null;
            }

            public void MarkDispatched(AdmittedExpansionJob job)
            {
                switch (job.Kind)
                {
                    case ParallelExpansionWorkProfile.Kind.Prepare:
                        _prepareDispatched = true;
                        _startedAt = Stopwatch.GetTimestamp();
                        break;
                    case ParallelExpansionWorkProfile.Kind.Action:
                        NextAction++;
                        break;
                    case ParallelExpansionWorkProfile.Kind.Potion:
                        NextPotion++;
                        break;
                    case ParallelExpansionWorkProfile.Kind.Choice:
                        if (job.Frontier != null)
                            job.Frontier.MarkContinuationDispatched();
                        else
                            Probes![job.ItemIndex] = null;
                        break;
                    case ParallelExpansionWorkProfile.Kind.PrimaryReplay:
                        job.Frontier!.MarkReplayDispatched(job.ReplayIndex, job.ReplayCount);
                        break;
                    case ParallelExpansionWorkProfile.Kind.Tail:
                        _tailDispatched = true;
                        break;
                    default:
                        throw new InvalidOperationException("非法的已准入作业类型。");
                }
            }

            public void Receive(AdmittedJobOutcome outcome)
            {
                AllocatedBytes = ParallelExpansionExecutor.SaturatingAdd(
                    AllocatedBytes, outcome.AllocatedBytes);
                if (outcome.Job.Kind == ParallelExpansionWorkProfile.Kind.Prepare)
                {
                    Actions = outcome.Actions
                        ?? throw new InvalidOperationException("父节点准备作业没有返回动作表。");
                    Potions = outcome.Potions
                        ?? throw new InvalidOperationException("父节点准备作业没有返回药水表。");
                    Probes = new DeferredCardActionProbe?[Actions.Count];
                    _actionBatches = new ExpansionBatch?[Actions.Count];
                    _actionCompleted = new bool[Actions.Count];
                    _potionBatches = new ExpansionBatch?[Potions.Count];
                    _potionCompleted = new bool[Potions.Count];
                    _cardFrontiers = new PrimaryChoiceReplayFrontier?[Actions.Count];
                    _potionFrontiers = new PrimaryChoiceReplayFrontier?[Potions.Count];
                }
                else if (outcome.Job.Kind == ParallelExpansionWorkProfile.Kind.Tail
                    || outcome.Job.Frontier?.IsEndTurn == true
                        && outcome.Job.Kind == ParallelExpansionWorkProfile.Kind.Choice)
                {
                    if (outcome.Frontier != null)
                    {
                        _endTurnFrontier = outcome.Frontier;
                        outcome.Frontier = null;
                        return;
                    }
                    _endTurnFrontier?.Dispose();
                    _endTurnFrontier = null;
                    _endTurnBatch = outcome.SerialCompletionOnly
                        ? null
                        : outcome.Batch
                            ?? throw new InvalidOperationException("回合尾部作业没有返回独占候选批次。");
                    outcome.Batch = null;
                    _endTurnBaselines = outcome.EndTurnBaselines;
                    _endTurnCompleted = true;
                }
                else if (outcome.Job.Kind == ParallelExpansionWorkProfile.Kind.PrimaryReplay)
                {
                    SimulationSnapshot?[] snapshots = outcome.ReplaySnapshots
                        ?? throw new InvalidOperationException("首层回放作业没有返回结果槽。");
                    for (int offset = 0; offset < snapshots.Length; offset++)
                    {
                        outcome.Job.Frontier!.Receive(outcome.Job.ReplayIndex + offset, snapshots[offset]);
                        snapshots[offset] = null;
                    }
                    outcome.ReplaySnapshots = null;
                }
                else if (outcome.Frontier != null)
                {
                    PrimaryChoiceReplayFrontier?[] frontiers = outcome.Frontier.IsPotion
                        ? _potionFrontiers! : _cardFrontiers!;
                    if (frontiers[outcome.Job.ItemIndex] != null)
                        throw new InvalidOperationException("同一动作重复发布选择 frontier。");
                    frontiers[outcome.Job.ItemIndex] = outcome.Frontier;
                    outcome.Frontier = null;
                }
                else if (outcome.Job.Kind == ParallelExpansionWorkProfile.Kind.Potion
                    || outcome.Job.Frontier?.IsPotion == true)
                {
                    _potionFrontiers![outcome.Job.ItemIndex]?.Dispose();
                    _potionFrontiers[outcome.Job.ItemIndex] = null;
                    int index = outcome.Job.ItemIndex;
                    _potionBatches![index] = outcome.SerialCompletionOnly
                        ? null
                        : outcome.Batch
                            ?? throw new InvalidOperationException("药水作业没有返回候选批次。");
                    outcome.Batch = null;
                    _potionCompleted![index] = true;
                    _completedPotions++;
                    while (_nextPotionAppend < Potions!.Count && _potionCompleted[_nextPotionAppend])
                    {
                        using ExpansionBatch? ready = _potionBatches[_nextPotionAppend];
                        if (ready != null)
                            foreach (SearchNode candidate in ready.Potions)
                                ready.TransferPotionTo(Aggregate!, candidate);
                        _potionBatches[_nextPotionAppend] = null;
                        _nextPotionAppend++;
                    }
                }
                else if (outcome.Probe != null)
                {
                    Probes![outcome.Job.ItemIndex] = outcome.Probe;
                    outcome.Probe = null;
                }
                else
                {
                    int index = outcome.Job.ItemIndex;
                    _cardFrontiers![index]?.Dispose();
                    _cardFrontiers[index] = null;
                    _actionBatches![index] = outcome.Batch
                        ?? throw new InvalidOperationException("动作/选择作业没有返回候选批次。");
                    outcome.Batch = null;
                    _actionCompleted![index] = true;
                    _completedActions++;
                    while (_nextAppend < Actions!.Count && _actionCompleted[_nextAppend])
                    {
                        using ExpansionBatch ready = _actionBatches[_nextAppend]!;
                        foreach (RawCardCandidate candidate in ready.Cards)
                            ready.TransferTo(Aggregate!, candidate);
                        _actionBatches[_nextAppend] = null;
                        _nextAppend++;
                    }
                }
                CompleteIfReady();
            }

            private void CompleteIfReady()
            {
                if (!_endTurnCompleted || _completedActions != Actions!.Count
                    || _completedPotions != Potions!.Count)
                    return;
                using ExpansionBatch? endTurn = _endTurnBatch;
                _endTurnBatch = null;
                if (endTurn != null)
                    foreach (SearchNode candidate in endTurn.EndTurns)
                        endTurn.TransferEndTurnTo(Aggregate!, candidate);
                if (_endTurnBaselines != null)
                    PublishCrossTurnStandPatBaselines(Node, _endTurnBaselines);
                _endTurnBaselines = null;
                TailCompleted = true;
                ElapsedTicks = Stopwatch.GetTimestamp() - _startedAt;
            }

            public void Dispose()
            {
                Aggregate?.Dispose();
                _endTurnBatch?.Dispose();
                _endTurnFrontier?.Dispose();
                if (_actionBatches != null)
                    foreach (ExpansionBatch? batch in _actionBatches)
                        batch?.Dispose();
                if (_potionBatches != null)
                    foreach (ExpansionBatch? batch in _potionBatches)
                        batch?.Dispose();
                if (Probes != null)
                    foreach (DeferredCardActionProbe? probe in Probes)
                        probe?.Dispose();
                if (_cardFrontiers != null)
                    foreach (PrimaryChoiceReplayFrontier? frontier in _cardFrontiers)
                        frontier?.Dispose();
                if (_potionFrontiers != null)
                    foreach (PrimaryChoiceReplayFrontier? frontier in _potionFrontiers)
                        frontier?.Dispose();
            }
        }

    private sealed class AdmittedJobOutcome(AdmittedExpansionJob job, CombatBeamSolver worker)
            : IDisposable
        {
            public AdmittedExpansionJob Job { get; } = job;
            public CombatBeamSolver Worker { get; } = worker;
            public List<PreparedCardAction>? Actions;
            public List<PreparedPotionAction>? Potions;
            public ExpansionBatch? Batch;
            public bool SerialCompletionOnly;
            public IReadOnlyList<CrossTurnStandPatBaseline>? EndTurnBaselines;
            public DeferredCardActionProbe? Probe;
            public PrimaryChoiceReplayFrontier? Frontier;
            public SimulationSnapshot?[]? ReplaySnapshots;
            public ExceptionDispatchInfo? Error;
            public long AllocatedBytes;
            public long ElapsedTicks;
            public int ActiveKindConcurrency;
            public void Dispose()
            {
                Batch?.Dispose();
                Probe?.Dispose();
                Frontier?.Dispose();
                if (ReplaySnapshots != null)
                    foreach (SimulationSnapshot? snapshot in ReplaySnapshots)
                        snapshot?.ReleaseSimulator();
            }
        }

    private sealed class AdmittedJobWave(int capacity) : IDisposable
        {
            private readonly object _gate = new();
            private readonly Queue<AdmittedJobOutcome> _completed = new(capacity);
            public CountdownEvent BackgroundCompleted { get; } = new(1);

            public void Publish(AdmittedJobOutcome outcome)
            {
                lock (_gate)
                {
                    _completed.Enqueue(outcome);
                    Monitor.Pulse(_gate);
                }
            }

            public AdmittedJobOutcome WaitForNextOutcome()
            {
                lock (_gate)
                {
                    while (_completed.Count == 0)
                        Monitor.Wait(_gate);
                    return _completed.Dequeue();
                }
            }

            public bool TryTake(out AdmittedJobOutcome? outcome)
            {
                lock (_gate)
                    return _completed.TryDequeue(out outcome);
            }

            public void Dispose()
            {
                while (TryTake(out AdmittedJobOutcome? outcome))
                    outcome!.Dispose();
                BackgroundCompleted.Dispose();
            }
        }

    private sealed record AdmittedExpansionJob(
            AdmittedParent Parent,
            ParallelExpansionWorkProfile.Kind Kind,
            int ItemIndex,
            PreparedCardAction? Action,
            PreparedPotionAction? Potion,
            DeferredCardActionProbe? Probe,
            AdmittedJobWave? Wave,
            int LaneIndex,
            PrimaryChoiceReplayFrontier? Frontier,
            int ReplayIndex,
            int ReplayCount) : ParallelExpansionExecutor.IExpansionLaneWorkItem
        {
            public void Execute(ParallelExpansionExecutor owner, CombatBeamSolver worker)
            {
                AdmittedJobOutcome outcome = new(this, worker);
                long allocatedAtStart = GC.GetAllocatedBytesForCurrentThread();
                long startedAt = Stopwatch.GetTimestamp();
                ParallelExpansionExecutor.UpdateMaximum(ref owner._maximumActiveWorkers,
                    Interlocked.Increment(ref owner._activeWorkers));
                if (Kind == ParallelExpansionWorkProfile.Kind.Action)
                    ParallelExpansionExecutor.UpdateMaximum(ref owner._maximumActiveActionReplayWorkers,
                        Interlocked.Increment(ref owner._activeActionReplayWorkers));
                if (Kind is ParallelExpansionWorkProfile.Kind.Choice
                    or ParallelExpansionWorkProfile.Kind.PrimaryReplay)
                    ParallelExpansionExecutor.UpdateMaximum(ref owner._maximumActiveChoiceWorkers,
                        Interlocked.Increment(ref owner._activeChoiceWorkers));
                if (Kind == ParallelExpansionWorkProfile.Kind.PrimaryReplay)
                    outcome.ActiveKindConcurrency = Interlocked.Increment(ref owner._activePrimaryReplayWorkers);
                try
                {
                    worker.SearchCancellationToken.ThrowIfCancellationRequested();
                    switch (Kind)
                    {
                        case ParallelExpansionWorkProfile.Kind.Prepare:
                            outcome.Actions = worker.PrepareCardActions(Parent.Node);
                            outcome.Potions = worker.PreparePotionActions(Parent.Node);
                            break;
                        case ParallelExpansionWorkProfile.Kind.Action:
                            PreparedCardActionEvaluation evaluation = worker.EvaluatePreparedCardAction(
                                Parent.Node, Action!.Value, seed: null, Parent.ForkGate);
                            outcome.Batch = evaluation.Batch;
                            outcome.Probe = evaluation.DeferredProbe;
                            break;
                        case ParallelExpansionWorkProfile.Kind.Choice:
                            if (Frontier?.IsEndTurn == true)
                            {
                                PreparedEndTurnEvaluation tail = worker.EvaluatePreparedEndTurn(
                                    Parent.Node, Parent.ForkGate, Frontier);
                                outcome.Batch = tail.Batch;
                                outcome.EndTurnBaselines = tail.Baselines;
                            }
                            else if (Frontier != null)
                            {
                                outcome.Batch = worker.CompletePrimaryChoices(Parent.Node, Frontier, Parent.ForkGate);
                            }
                            else
                            {
                                PreparedChoiceEvaluation choices = worker.EvaluateDeferredChoices(
                                    Parent.Node, Probe!, Parent.ForkGate);
                                outcome.Batch = choices.Batch;
                                outcome.Frontier = choices.Frontier;
                            }
                            break;
                        case ParallelExpansionWorkProfile.Kind.PrimaryReplay:
                            outcome.ReplaySnapshots = new SimulationSnapshot?[ReplayCount];
                            for (int offset = 0; offset < ReplayCount; offset++)
                                outcome.ReplaySnapshots[offset] = worker.ReplayPrimaryChoice(
                                    Parent.Node, Frontier!.Actions[ReplayIndex + offset], Parent.ForkGate,
                                    Frontier.EndTurn?.Layer.Branches[ReplayIndex + offset].PruneInvalidBranch ?? true,
                                    Frontier.EndTurn?.Checkpoint, Frontier.CardCheckpoint, Frontier.PotionCheckpoint, Frontier.EndTurn?.Layer.Checkpoint);
                            break;
                        case ParallelExpansionWorkProfile.Kind.Potion:
                            PreparedChoiceEvaluation potion = worker.EvaluatePreparedPotionAction(
                                Parent.Node, Potion!.Value, Parent.ForkGate);
                            outcome.Batch = potion.Batch;
                            outcome.Frontier = potion.Frontier;
                            break;
                        case ParallelExpansionWorkProfile.Kind.Tail:
                            PreparedEndTurnEvaluation endTurn =
                                worker.EvaluatePreparedEndTurn(Parent.Node, Parent.ForkGate);
                            outcome.Batch = endTurn.Batch;
                            outcome.EndTurnBaselines = endTurn.Baselines;
                            outcome.Frontier = endTurn.Frontier;
                            break;
                        default:
                            throw new InvalidOperationException("非法的已准入作业类型。");
                    }
                }
                catch (System.Exception error)
                {
                    // Preserve the error across the Thread boundary; the coordinator drains all
                    // jobs and rethrows. No failed action is skipped or accepted with a default.
                    outcome.Error = ExceptionDispatchInfo.Capture(error);
                }
                finally
                {
                    Probe?.Dispose();
                    Interlocked.Decrement(ref owner._activeWorkers);
                    if (Kind == ParallelExpansionWorkProfile.Kind.Action)
                        Interlocked.Decrement(ref owner._activeActionReplayWorkers);
                    if (Kind is ParallelExpansionWorkProfile.Kind.Choice
                        or ParallelExpansionWorkProfile.Kind.PrimaryReplay)
                        Interlocked.Decrement(ref owner._activeChoiceWorkers);
                    if (Kind == ParallelExpansionWorkProfile.Kind.PrimaryReplay)
                        Interlocked.Decrement(ref owner._activePrimaryReplayWorkers);
                    outcome.AllocatedBytes = Math.Max(
                        0, GC.GetAllocatedBytesForCurrentThread() - allocatedAtStart);
                    outcome.ElapsedTicks = Stopwatch.GetTimestamp() - startedAt;
                    Wave!.Publish(outcome);
                }
            }

            public void Signal() => Wave!.BackgroundCompleted.Signal();
        }
}
