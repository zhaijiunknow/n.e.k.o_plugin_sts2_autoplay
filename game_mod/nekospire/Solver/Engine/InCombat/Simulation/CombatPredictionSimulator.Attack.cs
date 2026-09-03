using MegaCrit.Sts2.Core.Commands.Builders;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Models;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Mirrors;

namespace CombatSolver.Engine.InCombat.Simulation;

internal sealed partial class CombatPredictionSimulator
{
    // Mirrors AttackContext for cards whose attack consists of custom CreatureCmd.Damage calls.
    public AttackCommand BeginAttackContext(PredictedCard cardSource, CardPlay cardPlay)
    {
        var command = new AttackCommand(0m)
            .FromCard(cardSource.Preview, cardPlay)
            .TargetingAllOpponents(State.CombatState);
        return BeginAttackContext(command);
    }

    public AttackCommand BeginAttackContext(AttackCommand command)
    {
        _ = command.Attacker
            ?? throw new InvalidOperationException("Attack context must have an attacker.");
        HookMirrors.BeforeAttack(this, command);
        return command;
    }

    public void AddAttackContextHit(
        AttackCommand attackContext,
        IReadOnlyList<DamageResult> results)
    {
        attackContext.IncrementHitsInternal();
        attackContext.AddResultsInternal(results);
    }

    public void EndAttackContext(AttackCommand attackContext)
    {
        Creature attacker = attackContext.Attacker
            ?? throw new InvalidOperationException("Attack context must have an attacker.");
        History.CreatureAttacked(
            attacker,
            attackContext.Results.SelectMany(results => results).ToArray());
        if (State.CombatState is ICombatPredictionCardEventSink eventSink)
            eventSink.RecordCreatureAttacked(attacker);
        HookMirrors.AfterAttack(this, attackContext);
    }

    /// <summary>
    /// Mirrors the prediction-relevant target and damage loop of <see cref="AttackCommand.Execute"/>.
    /// </summary>
    /// <remarks>
    /// The command must have an attacker, a configured single- or multi-target mode, and a card
    /// <see cref="AttackCommand.ModelSource"/>.
    /// Callers must already be inside that card's prediction trace; this method opens scopes only for hook listeners.
    /// </remarks>
    /// <exception cref="InvalidOperationException">Thrown when a required attacker, card source, or target mode is absent.</exception>
    public void ExecuteAttack(AttackCommand attackCommand)
    {
        if (attackCommand.Attacker is not { } attacker)
        {
            throw new InvalidOperationException("AttackCommand must have an attacker.");
        }

        // Prediction always mirrors a live-combat command, so the detached-combat exception in
        // AttackCommand.Execute does not apply here.
        if (IsOverOrEnding)
        {
            return;
        }

        if (attackCommand.ModelSource is not CardModel card)
        {
            throw new InvalidOperationException("AttackCommand simulation requires a card source.");
        }

        if (!attackCommand.IsSingleTargeted && !attackCommand.IsMultiTargeted)
        {
            throw new InvalidOperationException("AttackCommand must be either single-targeted or multi-targeted.");
        }

        var attackerState = State.GetCreature(attacker);
        if (attackerState.IsDead)
        {
            return;
        }

        HookMirrors.BeforeAttack(this, attackCommand);

        var hitCount = HookMirrors.ModifyAttackHitCount(this, attackCommand, GameRef.Get<int>(attackCommand, "_hitCount"));

        var cardSource = State.FindCard(card) ?? new PredictedCard(card);

        for (var i = 0; i < hitCount; i++)
        {
            if (attackerState.IsDead)
            {
                break;
            }

            var validTargets = GetPossibleAttackTargets(attackCommand)
                .Where(creature => State.GetCreature(creature).IsAlive)
                .ToList();
            if (validTargets.Count == 0)
            {
                break;
            }

            var singleTarget = SelectSingleAttackTarget(attackCommand, validTargets);
            if (attackCommand.IsRandomlyTargeted && singleTarget == null)
            {
                break;
            }

            var results = Damage(
                singleTarget != null ? [singleTarget] : validTargets,
                GetAttackDamageAmount(attackCommand, cardSource, singleTarget),
                attackCommand.DamageProps,
                attacker,
                cardSource,
                attackCommand.CardPlay);
            attackCommand.AddResultsInternal(results);
        }

        History.CreatureAttacked(
            attacker,
            attackCommand.Results.SelectMany(results => results).ToArray());
        if (State.CombatState is ICombatPredictionCardEventSink eventSink)
            eventSink.RecordCreatureAttacked(attacker);

        HookMirrors.AfterAttack(this, attackCommand);
    }

    // Mirrors AttackCommand.GetPossibleTargets but uses the simulator's state instead of the real CombatState.
    // Precondition: Execute has already verified that the command has an attacker and a target mode.
    private IReadOnlyList<Creature> GetPossibleAttackTargets(AttackCommand attackCommand)
    {
        if (attackCommand.Attacker is not { } attacker)
        {
            throw new InvalidOperationException("AttackCommand must have an attacker.");
        }

        if (attackCommand.IsSingleTargeted)
        {
            return [GameRef.Get<MegaCrit.Sts2.Core.Entities.Creatures.Creature>(attackCommand, "_singleTarget")!];
        }

        if (attackCommand.IsMultiTargeted)
        {
            return GameRef.Get<int>(attackCommand, "_sourceType") switch
            {
                0 => State.PlayerCreatures,
                _ => State.GetOpponentsOf(attacker)
            };
        }

        throw new InvalidOperationException("AttackCommand must be either single-targeted or multi-targeted.");
    }

    private Creature? SelectSingleAttackTarget(AttackCommand attackCommand, List<Creature> validTargets)
    {
        if (!attackCommand.IsRandomlyTargeted)
        {
            return validTargets.Count == 1 ? validTargets[0] : null;
        }

        if (!GameRef.Get<bool>(attackCommand, "_doesRandomTargetingAllowDuplicates"))
        {
            var previousReceivers = attackCommand.Results
                .SelectMany(results => results)
                .Select(result => result.Receiver)
                .ToHashSet();
            validTargets = validTargets
                .Where(creature => !previousReceivers.Contains(creature))
                .ToList();

            if (validTargets.Count == 0)
            {
                EngineDiagnostics.Warn("No valid targets available for randomly-targeted attack.");
                History.RecordRisk(PredictionRiskReason.MethodMirrorIncomplete);
                return null;
            }
        }

        return Rng.CombatTargets.NextItem(validTargets);
    }

    private decimal GetAttackDamageAmount(
        AttackCommand attackCommand,
        PredictedCard cardSource,
        Creature? singleTarget)
    {
        if (GameRef.Get<MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVar>(attackCommand, "_calculatedDamageVar") is {} calculatedDamageVar)
        {
            return calculatedDamageVar.InvokeCalculate(this, cardSource, singleTarget);
        }

        return GameRef.Get<decimal>(attackCommand, "_damagePerHit");
    }
}
