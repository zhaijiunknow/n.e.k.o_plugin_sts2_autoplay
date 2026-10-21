// Single source of truth for the four-way CARD-SIDE coverage report
// (exact / inferred / unsupported / ignored) that both solver paths publish as `coverage`.
//
// ── Why this file exists (T40) ────────────────────────────────────────────────────────────────────
// The two implementations used to disagree:
//   · Game/Sim/SimSolverPlan.cs  classified each card of `sim.Hand` by its own flags
//     (CostsX/CostsStarX -> ignored, BehaviorUnmodeled -> unsupported, …) => a real partition of the hand.
//   · Solver/CombatSolverFacade.cs mixed two DIFFERENT populations:
//         inferred    = Snapshot.PredictionGaps(Compensated) + Forecast.ApproximationDetails  (enemy items!)
//         unsupported = Snapshot.PredictionGaps(!Compensated) + Forecast.UnsupportedDetails  (enemy items!)
//         ignored     = 0                                                                     (hard-coded)
//         exact       = Math.Max(0, handCount - inferred - unsupported)                        (subtraction plug)
//     The numerator counted *every* distinct risk entry of the whole search (cards, powers, relics and
//     monsters), while the denominator was Snapshot.HandCount — the hand count of the FINAL simulated
//     snapshot (end of the planned horizon, see CombatBeamSolver.Phases.cs:493-513), not the live hand.
//     Measured consequence: hand = 1 card, `inferred` = 7, `exact` = 0 on every frame.
//
// ── The semantics this file fixes ───────────────────────────────────────────────────────────────
//   Denominator  = the hand this report is about. Callers pass it in (`SimCoverageCard[]`): for the
//                  CombatSolver path that is the LIVE hand captured on the game thread at the same
//                  moment as the root snapshot (== the root snapshot's hand, the hand the plan's
//                  current turn plays from); for the Sim path it is `SimState.Hand`. The denominator is
//                  never a *projected* hand and never a count of gap entries.
//   Numerator    = exactly one bucket per card of that hand (a true partition), so
//                  exact + inferred + unsupported + ignored == hand.Count always holds.
//   Gap evidence = a PredictionGap only counts for a card if its SourceId is that card's id, i.e. a
//                  gap is card-side evidence only when it is about a card IN the reported hand.
//                  Enemy-intent forecast items (Forecast.ApproximationDetails / UnsupportedDetails) are
//                  a different population: they are carried separately (EnemyApproximated /
//                  EnemyUnsupported) and NEVER enter the card-side buckets.
//   Precedence   = ignored > unsupported > inferred > exact, matching SimSolverPlan's original order.
//
//   ★★ 更正（2026-10-21，t42）：本文件原来在这里写着
//        "an X-cost card is excluded from the search entirely, so calling it 'ignored' is
//         the honest label regardless of anything else known about it"
//      —— 这句话是**事实错误**，已删除。实测两条引擎路径都**照常搜索并打出 X 费卡**，
//      只是能量成本按「当前全部能量」估算：
//        · 生产路径 `Solver/Search/CombatBeamSolver.StateEvaluation.cs`
//          （`CalculateReachableHandPotential`）：`combat.CanPlayCard` 不排除 X 费卡，
//          其 energyCost 取 `Math.Max(0, playerState.Energy)`；
//        · `Solver/Engine/InCombat/Simulation/CombatPredictionSimulator.Card.cs`：
//          `card.MutablePreview.EnergyCost.CapturedXValue = energyValue` —— 真的捕获 X 值去模拟；
//        · Sim 路径 `Game/Sim/SimResolver.cs`：`cost = card.CostsX ? state.ActiveEnergy : card.Cost`
//          且 `x = card.CostsX ? cost : -1` —— 同样按当前能量算、同样捕获 x。
//      ⇒ 因此 `ignored` 只是**覆盖度报告里的一个分类**（"这张牌的费用形态让本引擎不下钻到
//        精确效果"），**不等于该卡不参与搜索**。剩余风险仅是**效果保真度**
//        （例如 HEAVENLY_DRILL 的 X≥4 翻倍是否被建模），而不是"整块决策空间丢失"。
//
//      ★ 为什么这条更正值得留痕：队长的跨 mod 对照一度据此把 X 费卡判为"不参与搜索"，
//        并因此错误地建议关掉上游自动计算。注释与实现不一致会直接导致错误结论，
//        与 DEFECT-D5 同族 —— 所以下面 `SimTests` 里加了一条**文档回归门**钉住这句话不得回潮。
//
// Pure data types only (strings + bools) so game_mod/SimTests can compile and assert on this file.
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    /// <summary>One card of the reported hand, reduced to the facts the classifier needs.</summary>
    /// <param name="Id">Card model id (e.g. STRIKE). Matched ordinally against gap SourceIds.</param>
    /// <param name="CostsX">X energy cost (game: <c>card.EnergyCost.CostsX</c> / Sim: <c>SimCard.CostsX</c>).</param>
    /// <param name="CostsStarX">X star cost (game: <c>card.HasStarCostX</c> / Sim: <c>SimCard.CostsStarX</c>).</param>
    /// <param name="Unmodeled">Known to be un-simulatable (Sim path: <c>SimCard.BehaviorUnmodeled</c>); always false for the CombatSolver path.</param>
    /// <param name="Approximated">Known to be approximated (Sim path: summon/script/<c>ApproximateEffect</c>); always false for the CombatSolver path.</param>
    public readonly record struct SimCoverageCard(
        string Id,
        bool CostsX,
        bool CostsStarX,
        bool Unmodeled,
        bool Approximated);

    /// <summary>
    /// One recorded prediction gap, reduced to (who, was it compensated). The source may be a card, a
    /// power, a relic or a monster; <see cref="SimCoverage.Classify"/> only uses it when
    /// <paramref name="SourceId"/> equals a card id of the reported hand.
    /// </summary>
    public readonly record struct SimCoverageGap(string SourceId, bool Compensated);

    /// <summary>Four-way bucket a single hand card falls into.</summary>
    public enum SimCoverageClass
    {
        Exact = 0,
        Inferred = 1,
        Unsupported = 2,
        Ignored = 3,
    }

    /// <summary>
    /// Result of one card-side coverage pass. <see cref="Exact"/> is a real count of hand cards — never a
    /// subtraction plug. When the hand could not be captured at all, the whole report is marked unknown
    /// (<see cref="IsKnown"/> false, <see cref="Exact"/> == <see cref="UnknownValue"/>) instead of
    /// inventing a plausible-looking number.
    /// </summary>
    public readonly struct SimCoverageSummary
    {
        /// <summary>Sentinel for "unknown": the hand could not be captured, so nothing can be counted.</summary>
        public const int UnknownValue = -1;

        public SimCoverageSummary(
            int exact,
            int inferred,
            int unsupported,
            int ignored,
            int handCount,
            int enemyApproximated,
            int enemyUnsupported,
            bool isKnown)
        {
            Exact = exact;
            Inferred = inferred;
            Unsupported = unsupported;
            Ignored = ignored;
            HandCount = handCount;
            EnemyApproximated = enemyApproximated;
            EnemyUnsupported = enemyUnsupported;
            IsKnown = isKnown;
        }

        /// <summary>Hand cards for which no gap and no model flag says anything is approximated.</summary>
        public int Exact { get; }

        public int Inferred { get; }

        public int Unsupported { get; }

        /// <summary>X / star-X cost cards: excluded from the search, so their coverage is undefined.</summary>
        public int Ignored { get; }

        /// <summary>The denominator: number of cards in the hand this report is about. -1 when unknown.</summary>
        public int HandCount { get; }

        /// <summary>Enemy-intent forecast approximations (Forecast.ApproximationDetails). Not card-side.</summary>
        public int EnemyApproximated { get; }

        /// <summary>Unsupported enemy intents (Forecast.UnsupportedDetails). Not card-side.</summary>
        public int EnemyUnsupported { get; }

        public bool IsKnown { get; }

        /// <summary>Cards that received one of the four buckets.</summary>
        public int Classified => Exact + Inferred + Unsupported + Ignored;

        /// <summary>True when the four buckets exactly partition the denominator (the invariant).</summary>
        public bool IsConsistent => IsKnown && Classified == HandCount;

        public static SimCoverageSummary Unknown(int enemyApproximated = 0, int enemyUnsupported = 0)
            => new(UnknownValue, 0, 0, 0, UnknownValue, enemyApproximated, enemyUnsupported, isKnown: false);
    }

    public static class SimCoverage
    {
        /// <summary>
        /// Classify ONE hand card. Precedence: ignored (X / star-X cost) → unsupported → inferred → exact.
        /// A gap only counts when it is about this card (<c>gap.SourceId == card.Id</c>); an uncompensated
        /// card gap wins over a compensated one, and a card is counted once even with several gaps.
        /// </summary>
        public static SimCoverageClass Classify(SimCoverageCard card, IReadOnlyList<SimCoverageGap>? gaps)
        {
            // X / star-X cost: excluded from the search (SimSolverPlan.cs:112 has the same first test).
            if (card.CostsX || card.CostsStarX)
                return SimCoverageClass.Ignored;

            bool unsupported = card.Unmodeled;
            bool inferred = card.Approximated;
            if (gaps != null)
            {
                for (var i = 0; i < gaps.Count; i++)
                {
                    SimCoverageGap gap = gaps[i];
                    if (!string.Equals(gap.SourceId, card.Id, StringComparison.Ordinal))
                        continue;   // power / relic / monster / other-card evidence — not this card
                    if (gap.Compensated)
                        inferred = true;
                    else
                        unsupported = true;
                }
            }

            if (unsupported)
                return SimCoverageClass.Unsupported;
            if (inferred)
                return SimCoverageClass.Inferred;
            return SimCoverageClass.Exact;
        }

        /// <summary>
        /// Classify the whole hand. Enemy forecast items are accepted so the caller does not have to
        /// branch, but they are carried on their own fields and never change the card-side buckets.
        /// Pass <paramref name="hand"/> == null when the live hand could not be captured: the result is
        /// then explicitly unknown (<see cref="SimCoverageSummary.UnknownValue"/>) rather than a fake 0.
        /// </summary>
        public static SimCoverageSummary Summarize(
            IReadOnlyList<SimCoverageCard>? hand,
            IReadOnlyList<SimCoverageGap>? gaps,
            int enemyApproximated = 0,
            int enemyUnsupported = 0)
        {
            if (hand == null)
                return SimCoverageSummary.Unknown(enemyApproximated, enemyUnsupported);

            var exact = 0;
            var inferred = 0;
            var unsupported = 0;
            var ignored = 0;
            for (var i = 0; i < hand.Count; i++)
            {
                switch (Classify(hand[i], gaps))
                {
                    case SimCoverageClass.Ignored:
                        ignored++;
                        break;
                    case SimCoverageClass.Unsupported:
                        unsupported++;
                        break;
                    case SimCoverageClass.Inferred:
                        inferred++;
                        break;
                    default:
                        exact++;
                        break;
                }
            }

            return new SimCoverageSummary(
                exact, inferred, unsupported, ignored, hand.Count, enemyApproximated, enemyUnsupported, isKnown: true);
        }

        /// <summary>
        /// Adapt a pure Sim card to the classifier input, with the Sim pipeline's criteria:
        /// BehaviourUnmodeled → unsupported; SummonCardId / OnPlayScript / ApproximateEffect → inferred;
        /// CostsX / CostsStarX → ignored. (Identical to SimSolverPlan's original per-card rule.)
        /// </summary>
        public static SimCoverageCard FromSimCard(SimCard card)
        {
            if (card == null) throw new ArgumentNullException(nameof(card));
            return new SimCoverageCard(
                card.Id ?? string.Empty,
                card.CostsX,
                card.CostsStarX,
                card.BehaviorUnmodeled,
                card.SummonCardId != null || card.OnPlayScript != null || card.ApproximateEffect);
        }
    }
}
