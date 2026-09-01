using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Powers;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Powers;
using CombatSolver.Engine.Common;

namespace CombatSolver;

internal readonly record struct StrategicEffectVector(
    int DamagePotential,
    int PreventionPotential,
    int ResourcePotential,
    int CardAccessPotential,
    int ScalingPotential)
{
    public static StrategicEffectVector Zero { get; } = new(0, 0, 0, 0, 0);

    public int RetentionValue => SaturatingSum(
        DamagePotential,
        PreventionPotential,
        ResourcePotential,
        CardAccessPotential,
        ScalingPotential);

    public static StrategicEffectVector operator +(
        StrategicEffectVector left,
        StrategicEffectVector right)
        => new(
            SaturatingAdd(left.DamagePotential, right.DamagePotential),
            SaturatingAdd(left.PreventionPotential, right.PreventionPotential),
            SaturatingAdd(left.ResourcePotential, right.ResourcePotential),
            SaturatingAdd(left.CardAccessPotential, right.CardAccessPotential),
            SaturatingAdd(left.ScalingPotential, right.ScalingPotential));

    private static int SaturatingSum(params int[] values)
    {
        long total = 0;
        foreach (int value in values)
            total += value;
        return (int)Math.Clamp(total, 0L, int.MaxValue);
    }

    private static int SaturatingAdd(int left, int right)
        => (int)Math.Clamp((long)left + right, 0L, int.MaxValue);
}

internal readonly record struct StrategicEffectContext(
    int EnemyHp,
    int IncomingDamage,
    int IncomingHitCount,
    int RemainingTurns,
    int UsefulCardPlays,
    int AttackPlays,
    int SkillPlays,
    int BlockSkillPlays,
    int PowerPlays,
    int ExhaustPlays,
    int ShivPlays,
    int DebuffApplications,
    int SkillEnergySpend,
    int PowerEnergySpend,
    int AverageCardValue,
    int BestCardValue,
    int AverageAttackValue,
    int StatusDrawTriggers)
{
    public static StrategicEffectContext Build(
        IReadOnlyList<PredictedCard> liveCards,
        int enemyHp,
        int incomingDamage,
        int incomingHitCount)
    {
        int attackCount = 0;
        int skillCount = 0;
        int blockSkillCount = 0;
        int powerCount = 0;
        int exhaustCount = 0;
        int shivCount = 0;
        int debuffCount = 0;
        int statusCount = 0;
        int skillEnergy = 0;
        int powerEnergy = 0;
        int totalCardValue = 0;
        int bestCardValue = 0;
        int totalAttackValue = 0;
        foreach (PredictedCard predicted in liveCards)
        {
            CardModel card = predicted.Preview;
            int cardValue = Math.Max(1, (int)Math.Ceiling(CardChoiceSupport.CardValue(card)));
            totalCardValue += cardValue;
            bestCardValue = Math.Max(bestCardValue, cardValue);
            int energyCost = card.EnergyCost.CostsX
                ? 0
                : Math.Max(0, card.EnergyCost.GetWithModifiers(CostModifiers.All));
            switch (card.Type)
            {
                case CardType.Attack:
                    attackCount++;
                    totalAttackValue += cardValue;
                    break;
                case CardType.Skill:
                    skillCount++;
                    skillEnergy += energyCost;
                    if (card.DynamicVars.Keys.Any(IsBlockDynamicVar))
                        blockSkillCount++;
                    break;
                case CardType.Power:
                    powerCount++;
                    powerEnergy += energyCost;
                    break;
                case CardType.Status:
                    statusCount++;
                    break;
            }
            if (card.Keywords.Contains(CardKeyword.Exhaust))
                exhaustCount++;
            if (card.Tags.Contains(CardTag.Shiv))
                shivCount++;
            if (card.DynamicVars.Keys.Any(IsDebuffDynamicVar))
                debuffCount++;
        }

        int actualDeckSize = liveCards.Count;
        int deckSize = Math.Max(1, actualDeckSize);
        int cardsPerTurn = Math.Min(10, Math.Max(1, Math.Min(5, deckSize)));
        int damagePerCycle = Math.Max(1, totalAttackValue);
        int estimatedCycles = (int)Math.Ceiling((double)Math.Max(1, enemyHp) / damagePerCycle);
        int remainingTurns = Math.Clamp(
            estimatedCycles * Math.Max(1, (int)Math.Ceiling(deckSize / 5d)),
            1,
            SolverWeights.SetupValueHorizonTurns);
        int reachableCards = actualDeckSize == 0
            ? 0
            : Math.Min(deckSize * 2, remainingTurns * cardsPerTurn);
        int attackPlays = ReachablePlays(attackCount, deckSize, reachableCards);
        int skillPlays = ReachablePlays(skillCount, deckSize, reachableCards);
        int blockSkillPlays = ReachablePlays(blockSkillCount, deckSize, reachableCards);
        int powerPlays = Math.Min(powerCount, reachableCards);
        int exhaustPlays = Math.Min(exhaustCount, reachableCards);
        int shivPlays = ReachablePlays(shivCount, deckSize, reachableCards);
        int debuffApplications = ReachablePlays(debuffCount, deckSize, reachableCards);
        return new StrategicEffectContext(
            Math.Max(0, enemyHp),
            Math.Max(0, incomingDamage),
            Math.Max(0, incomingHitCount),
            remainingTurns,
            reachableCards,
            attackPlays,
            skillPlays,
            blockSkillPlays,
            powerPlays,
            exhaustPlays,
            shivPlays,
            debuffApplications,
            skillEnergy,
            powerEnergy,
            Math.Max(1, totalCardValue / deckSize),
            Math.Max(1, bestCardValue),
            attackCount == 0 ? 0 : Math.Max(1, totalAttackValue / attackCount),
            Math.Min(remainingTurns, ReachablePlays(statusCount, deckSize, reachableCards)));
    }

    private static int ReachablePlays(int matchingCards, int deckSize, int reachableCards)
        => matchingCards == 0
            ? 0
            : Math.Max(1, (int)Math.Ceiling((double)matchingCards * reachableCards / deckSize));

    private static bool IsDebuffDynamicVar(string key)
        => key.Contains("Weak", StringComparison.OrdinalIgnoreCase)
            || key.Contains("Vulnerable", StringComparison.OrdinalIgnoreCase)
            || key.Contains("Poison", StringComparison.OrdinalIgnoreCase)
            || key.Contains("Doom", StringComparison.OrdinalIgnoreCase)
            || key.Contains("Debuff", StringComparison.OrdinalIgnoreCase)
            || key.Contains("StrengthLoss", StringComparison.OrdinalIgnoreCase);

    private static bool IsBlockDynamicVar(string key)
        => key.Contains("Block", StringComparison.OrdinalIgnoreCase);
}

internal static class StrategicEffectModel
{
    public static StrategicEffectVector Evaluate(
        PowerModel power,
        StrategicEffectContext context)
    {
        int amount = Math.Max(1, power.Amount);
        int enemyHp = context.EnemyHp;
        int energyUnit = Math.Max(3, context.AverageCardValue);
        int cardAccessUnit = context.AverageCardValue;
        return power switch
        {
            AfterimagePower => Prevention(amount * context.UsefulCardPlays, context),
            BufferPower => Prevention(BufferPrevention(amount, context), context),
            FeelNoPainPower => Prevention(amount * context.ExhaustPlays, context),
            ThornsPower => Damage(amount * context.IncomingHitCount, enemyHp),
            EchoFormPower when context.UsefulCardPlays > 0 => Damage(
                context.BestCardValue
                    * Math.Min(amount, Math.Max(1, context.UsefulCardPlays))
                    * context.RemainingTurns,
                enemyHp),
            EchoFormPower => StrategicEffectVector.Zero,
            EnvenomPower => Damage(amount * context.AttackPlays, enemyHp),
            AccuracyPower => Damage(amount * context.ShivPlays, enemyHp),
            SleightOfFleshPower => Damage(amount * context.DebuffApplications, enemyHp),
            StrengthPower => Damage(amount * context.AttackPlays, enemyHp),
            LethalityPower when context.AttackPlays > 0 => Damage(
                context.AverageAttackValue
                    * Math.Min(context.AttackPlays, context.RemainingTurns)
                    * amount / 100,
                enemyHp),
            ReaperFormPower when context.AttackPlays > 0 => Damage(
                context.AverageAttackValue * context.AttackPlays * amount,
                enemyHp),
            LethalityPower or ReaperFormPower => StrategicEffectVector.Zero,
            DexterityPower => Prevention(amount * context.BlockSkillPlays, context),
            DemonFormPower when context.AttackPlays > 0 => Damage(
                amount * Math.Max(1, context.AttackPlays / Math.Max(1, context.RemainingTurns))
                    * context.RemainingTurns * (context.RemainingTurns + 1) / 2,
                enemyHp),
            DemonFormPower => StrategicEffectVector.Zero,
            CuriousPower => Resource(
                Math.Min(context.PowerEnergySpend, amount * context.PowerPlays) * energyUnit),
            CorruptionPower => Resource(context.SkillEnergySpend * energyUnit),
            CreativeAiPower => CardAccess(
                amount * context.RemainingTurns * cardAccessUnit),
            IterationPower => CardAccess(
                amount * context.StatusDrawTriggers * cardAccessUnit),
            MasterPlannerPower => CardAccess(context.SkillPlays * cardAccessUnit),
            FocusPower => Scaling(amount * context.RemainingTurns * 2),
            FurnacePower => Scaling(amount * context.RemainingTurns * 2),
            ThunderPower => Damage(amount * context.RemainingTurns, enemyHp),
            LightningRodPower => Scaling(amount * context.RemainingTurns),
            _ => Scaling(amount),
        };
    }

    private static StrategicEffectVector Damage(int value, int enemyHp)
        => new(Math.Min(Math.Max(0, enemyHp), Math.Max(0, value)), 0, 0, 0, 0);

    private static StrategicEffectVector Prevention(int value, StrategicEffectContext context)
    {
        int cap = context.IncomingDamage == 0
            ? value
            : context.IncomingDamage * Math.Min(2, context.RemainingTurns);
        return new(0, Math.Min(Math.Max(0, cap), Math.Max(0, value)), 0, 0, 0);
    }

    private static int BufferPrevention(int amount, StrategicEffectContext context)
    {
        if (context.IncomingDamage == 0)
            return amount * 8;
        int averageHit = Math.Max(
            1,
            context.IncomingDamage / Math.Max(1, context.IncomingHitCount));
        return Math.Min(context.IncomingDamage, amount * averageHit);
    }

    private static StrategicEffectVector Resource(int value)
        => new(0, 0, Math.Max(0, value), 0, 0);

    private static StrategicEffectVector CardAccess(int value)
        => new(0, 0, 0, Math.Max(0, value), 0);

    private static StrategicEffectVector Scaling(int value)
        => new(0, 0, 0, 0, Math.Max(0, value));
}
