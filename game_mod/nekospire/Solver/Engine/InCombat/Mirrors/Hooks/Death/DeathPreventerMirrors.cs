using MegaCrit.Sts2.Core.Models.Potions;
using MegaCrit.Sts2.Core.Models.Relics;
using CombatSolver.Engine.Common;
using CombatSolver.Engine.InCombat.Simulation;

namespace CombatSolver.Engine.InCombat.Mirrors.Hooks.Death;

internal static class FairyInABottleMirrors
{
    public static bool ShouldDie(FairyInABottle potion, ShouldDieMirrorContext context)
        => context.Creature != potion.Owner.Creature;

    public static void AfterPreventingDeath(
        FairyInABottle potion,
        AfterPreventingDeathMirrorContext context)
    {
        if (context.CombatState is not ICombatPredictionEffectSink effects)
            throw new InvalidOperationException("瓶中仙女结算缺少可写的预测状态。");
        effects.ConsumePotion(potion);
        effects.BeforePotionUsed(context.Simulator, potion, context.Creature);
        int maxHp = context.State.GetCreature(context.Creature).MaxHp;
        context.Simulator.Heal(context.Creature, HealAmount(maxHp));
        if (context.State.GetCreature(context.Creature).IsAlive)
            effects.AfterPotionUsed(context.Simulator, potion, context.Creature);
    }

    internal static decimal HealAmount(int maxHp) => Math.Max(1m, maxHp * 0.3m);
}

internal static class LizardTailMirrors
{
    public static bool ShouldDieLate(LizardTail relic, ShouldDieMirrorContext context)
    {
        if (context.Creature == relic.Owner.Creature)
        {
            return GetState(relic, context).WasUsed;
        }

        return true;
    }

    public static void AfterPreventingDeath(LizardTail relic, AfterPreventingDeathMirrorContext context)
    {
        GetState(relic, context).WasUsed = true;
        if (context.Simulator.IsRecordingActionRelicTriggers)
            context.Simulator.RecordRelicTrigger(relic, "：复活");

        int maxHp = context.State.GetCreature(context.Creature).MaxHp;
        context.Simulator.Heal(context.Creature, HealAmount(relic, maxHp));
    }

    internal static decimal HealAmount(LizardTail relic, int maxHp)
        => Math.Max(1m, maxHp * (relic.DynamicVars.Heal.BaseValue / 100m));

    internal static bool WasUsed(LizardTail relic, CombatPredictionSimulator simulator)
        => simulator.StateStore
            .Peek(relic, () => new LizardTailPredictionState(relic))
            .WasUsed;

    private static LizardTailPredictionState GetState(LizardTail relic, CombatMirrorContext context)
        => context.StateStore.Get(relic, () => new LizardTailPredictionState(relic));
}

internal sealed class LizardTailPredictionState(LizardTail relic) : IPredictionStateForkable
{
    public bool WasUsed { get; set; } = relic.WasUsed;

    public object Fork(PredictionForkContext context) => MemberwiseClone();
}
