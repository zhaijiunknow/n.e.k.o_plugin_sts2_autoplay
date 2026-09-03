using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.ValueProps;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver;

internal readonly record struct LiveEndTurnRiskProjection(
    int HpBefore,
    int HpAfter,
    int HpLost,
    bool PlayerDead,
    string MonsterMoves);

internal static class LiveEndTurnRiskEvaluator
{
    public static LiveEndTurnRiskProjection Evaluate(
        CombatState state,
        IReadOnlyList<PlanCardChoice>? turnStartChoices)
    {
        Player player = LocalContext.GetMe(state)
            ?? throw new InvalidOperationException("结束回合复核找不到本地玩家。");
        SimulatedCombatState combat = new(state);
        return Evaluate(player, combat, turnStartChoices);
    }

    internal static LiveEndTurnRiskProjection Evaluate(
        Player player,
        SimulatedCombatState combat,
        IReadOnlyList<PlanCardChoice>? turnStartChoices)
    {
        PlanCardChoice[] endTurnChoices = turnStartChoices?
            .Where(choice => choice.Timing is PlanChoiceTiming.PlayerTurnEnd or PlanChoiceTiming.EnemyTurn)
            .ToArray() ?? [];
        PlanCardChoice[] cursorChoices = endTurnChoices
            .Where(choice => choice.Effect != PlanChoiceEffect.ApplyKnowledgeCurse)
            .ToArray();
        combat.BeginActionChoices(cursorChoices);
        combat.SetActionChoiceTiming(PlanChoiceTiming.PlayerTurnEnd);
        try
        {
            LiveEndTurnRiskProjection projection = EvaluateCore(player, combat, endTurnChoices);
            if (combat.HasPendingChoice)
                throw new InvalidOperationException("结束回合复核产生了路线未提供的选牌。");
            return projection;
        }
        finally
        {
            combat.EndActionChoices();
        }
    }

    private static LiveEndTurnRiskProjection EvaluateCore(
        Player player,
        SimulatedCombatState combat,
        IReadOnlyList<PlanCardChoice> endTurnChoices)
    {
        CombatPredictionSimulator simulator = new(combat);
        SimCreatureState simulatedPlayer = simulator.State.GetCreature(player.Creature);
        int hpBefore = simulatedPlayer.CurrentHp;
        HashSet<uint> processedEnemyDeaths = [];
        bool paelsEyeTriggers = player.Relics
            .OfType<PaelsEye>()
            .Any(relic => !relic.IsMelted && relic.ShouldTakeExtraTurn(player));
        bool takingExtraTurn = combat.PrepareLiveExtraPlayerTurn(
            simulator,
            player,
            paelsEyeTriggers);
        int etherealExhaustCount = combat.CountEtherealCardsInHand(simulator, player);

        PlayerTurnEndLifecycle.RunPhaseOne(
            simulator,
            combat,
            player,
            [player.Creature]);
        combat.CommitHistoryCourseTurn(player);
        combat.NormalizeAeonglassWithers(simulator);
        combat.NormalizeCardAfflictions(simulator);
        CorePowerSupport.ApplyEnemyDeathPowers(
            simulator,
            combat,
            combat.KnownEnemies,
            processedEnemyDeaths);
        CorePowerSupport.FlushPlayerHandAtTurnEnd(simulator, combat, player);
        TurnStartRelicSupport.TriggerAfterSideTurnEnd(
            simulator,
            combat,
            [player.Creature],
            etherealExhaustCount);
        CorePowerSupport.TriggerPlayerSideTurnEndEffects(
            simulator,
            combat,
            [player.Creature],
            etherealExhaustCount);
        CorePowerSupport.ApplyEnemyDeathPowers(
            simulator,
            combat,
            combat.KnownEnemies,
            processedEnemyDeaths);

        if (takingExtraTurn || simulatedPlayer.IsDead)
            return BuildProjection(hpBefore, simulatedPlayer, []);

        combat.CurrentSide = CombatSide.Enemy;
        combat.SetActionChoiceTiming(PlanChoiceTiming.EnemyTurn);
        combat.SnapshotPowerAmountsAtTurnStart(combat.Enemies);
        TurnStartRelicSupport.TriggerBeforeSideTurnStart(simulator, combat, combat.Enemies);
        TurnStartPowerSupport.TriggerBeforeSideTurnStart(simulator, combat, combat.Enemies);
        foreach (Creature enemy in combat.Enemies)
        {
            SimCreatureState simulatedEnemy = simulator.State.GetCreature(enemy);
            if (simulatedEnemy.Block > 0)
            {
                if (combat.ShouldClearBlock(enemy, out AbstractModel? preventer))
                    simulatedEnemy.DamageBlock(simulatedEnemy.Block, ValueProp.Move);
                else
                    PersistentRelicSupport.TriggerAfterPreventingBlockClear(simulator, preventer, enemy);
            }
            CorePowerSupport.TriggerAfterBlockCleared(simulator, combat, enemy);
        }
        combat.TriggerSideTurnStart(
            simulator,
            CombatSide.Enemy,
            combat.Enemies,
            decrementPlating: combat.RoundNumber > 1);
        int poisonHistoryStart = simulator.History.Entries.Count;
        CorePowerSupport.TriggerPoison(simulator, combat, combat.Enemies.ToArray());
        TriggeredPowerSupport.CompensateHistorySince(simulator, combat, poisonHistoryStart);
        CorePowerSupport.ApplyEnemyDeathPowers(
            simulator,
            combat,
            combat.KnownEnemies,
            processedEnemyDeaths);

        Creature[] actingEnemies = combat.Enemies.ToArray();
        List<ForecastMove> moves = new(actingEnemies.Length);
        foreach (Creature actingEnemy in actingEnemies)
        {
            if (!combat.CanPerformMonsterMove(simulator, actingEnemy))
                continue;
            ForecastMove move = combat.CurrentMonsterMove(actingEnemy);
            moves.Add(move);
            if (combat.ConsumeStunNextMove(actingEnemy))
                continue;
            if (combat.TryConsumeForcedMonsterMove(actingEnemy, out string forcedMove, out int forcedDamage))
            {
                if (forcedMove == "EXPLODE_MOVE")
                {
                    MonsterMoveSemantics.DamagePlayer(
                        simulator,
                        combat,
                        move.Owner,
                        player.Creature,
                        forcedDamage);
                }
                if (simulatedPlayer.IsDead)
                    break;
                continue;
            }
            if (MonsterMoveSemantics.ApplyForecastMove(
                    simulator,
                    combat,
                    move,
                    player.Creature,
                    processedEnemyDeaths,
                    endTurnChoices))
            {
                break;
            }
        }

        return BuildProjection(hpBefore, simulatedPlayer, moves);
    }

    private static LiveEndTurnRiskProjection BuildProjection(
        int hpBefore,
        SimCreatureState simulatedPlayer,
        IReadOnlyList<ForecastMove> moves)
    {
        int hpAfter = simulatedPlayer.CurrentHp;
        return new LiveEndTurnRiskProjection(
            hpBefore,
            hpAfter,
            Math.Max(0, hpBefore - hpAfter),
            simulatedPlayer.IsDead,
            string.Join(',', moves.Select(move =>
                $"{move.Owner.Monster?.Id.Entry ?? "?"}:{move.Move.Id}")));
    }
}
