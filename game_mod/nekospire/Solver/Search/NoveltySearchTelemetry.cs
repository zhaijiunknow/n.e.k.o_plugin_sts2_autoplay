namespace CombatSolver;

internal sealed record NoveltySearchImprovement(long ElapsedMs, int Expanded, int Loss, int Potions, int Turn);

internal sealed record NoveltySearchTelemetry(
    long Generated, int PeakOpen, int HorizonLeaves, int NonNovelPruned,
    int FamiliarAdmitted, int OpenDropped, int Wins, long? FirstVictoryMs,
    int? FirstVictoryExpanded, int? MinimumCumulativeLoss, int? MinimumLossExpanded,
    long? MinimumLossMs, string Stop, int NoveltyEntries, int NoveltyAtoms,
    int NoveltyPartitions, long Unary, long Binary, long Familiar,
    IReadOnlyList<NoveltySearchImprovement> Improvements);

/// <summary>
/// 一条被选入续搜的前沿开局在后续完整搜索里取得的单次最好成绩。
/// 只记录纯值供遥测聚合：是否击败了发出探索时的 incumbent（<c>Improved</c>），
/// 以及这次续搜结束时自己的完整胜利状态。若没有完整搜索结果（例如用药政策不满足或结果交接），
/// <c>Completed</c> 为 false，路线成绩保持 null；耗时和展开仍记录实际观测值。
/// </summary>
internal sealed record EarlyTurnContinuationImprovement(
    int CompletedTurns,
    int Rank,
    long ElapsedMs,
    long Expanded,
    bool Completed,
    bool Improved,
    bool? Won,
    int? Loss,
    int? Potions,
    int? EndedTurn);

/// <summary>
/// 一次早期回合探索的整体遥测：侦察消耗、前沿派发统计，以及每个
/// <c>EarlyTurnContinuation</c> 续搜的最好成绩序列（按派发顺序）。
/// <c>Improvements</c> 统计严格击败当时已选路线的续搜结果。
/// 全部字段都是纯值诊断，不进入战斗状态键或终局排序。
/// </summary>
internal sealed record EarlyTurnExplorationTelemetry(
    int ScoutExpanded,
    int FrontierCandidates,
    int Attempted,
    int Improvements,
    int? BestLoss,
    int? BestPotions,
    int? BestEndedTurn,
    int? FirstImprovementDepth,
    int? FirstImprovementRank,
    long Expanded,
    string Stop,
    IReadOnlyList<EarlyTurnContinuationImprovement> Continuations);

internal sealed record NoveltyPortfolioTelemetry(
    string Stop, long ExplorationExpanded, long ExplorationElapsed,
    NoveltySearchTelemetry? ExplorationDetails, bool BaselineRan, string Selected,
    int? ExplorationLoss, bool ExplorationWon, int? BaselineLoss, bool BaselineWon,
    int RemainingNodes, int RemainingMs);
