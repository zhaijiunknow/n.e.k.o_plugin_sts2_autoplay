using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Combat.History.Entries;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Afflictions;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;
using CombatSolver.Engine.Common;

namespace CombatSolver.Engine.InCombat.Mirrors.Hooks.Card;

// Shadow model state shared by card-play lifecycle hooks and their downstream value/predicate hook mirrors.
internal sealed class CounterPredictionState(int value) : IPredictionStateForkable
{
    public int Value { get; set; } = value;

    public object Fork(PredictionForkContext context) => MemberwiseClone();
}

internal sealed class ChainsOfBindingPredictionState(ChainsOfBindingPower power) : IPredictionStateForkable
{
    public bool BoundCardPlayed { get; set; } =
        (bool)((bool)(GameRef.Get(GameRef.InvokeGeneric(power, "GetInternalData", "Data"), "boundCardPlayed")));

    public int BoundCardsAfflictedThisTurn { get; set; } =
        CombatManager.Instance.History.Entries
            .OfType<CardAfflictedEntry>()
            .Count(entry =>
                entry.HappenedThisTurn(power.CombatState)
                && entry.Actor == power.Owner
                && entry.Affliction is Bound);

    public object Fork(PredictionForkContext context) => MemberwiseClone();
}

internal sealed class SurroundedPredictionState(SurroundedPower power) : IPredictionStateForkable
{
    public SurroundedPower.Direction Facing { get; set; } = power.Facing;

    public object Fork(PredictionForkContext context) => MemberwiseClone();
}

internal sealed class PenNibPredictionState(PenNib relic) : IPredictionStateForkable, IPredictionForkBoundary
{
    public int AttacksPlayed { get; set; } = relic.AttacksPlayed;

    public CardModel? AttackToDouble { get; set; }

    public object Fork(PredictionForkContext context)
    {
        AssertForkable();
        return MemberwiseClone();
    }

    public void AssertForkable()
    {
        if (AttackToDouble is not null)
            throw new InvalidOperationException("Cannot fork Pen Nib during card-play resolution.");
    }
}

internal sealed class PaelsLegionPredictionState(PaelsLegion relic)
    : IPredictionStateForkable, IPredictionForkBoundary
{
    public int Cooldown { get; set; } = GameRef.Get<int>(relic, "_cooldown");

    public bool TriggeredBlockLastTurn { get; set; } = GameRef.Get<bool>(relic, "_triggeredBlockLastTurn");

    public CardPlay? AffectedCardPlay { get; set; } = GameRef.Get<MegaCrit.Sts2.Core.Entities.Cards.CardPlay>(relic, "_affectedCardPlay");

    public object Fork(PredictionForkContext context)
    {
        AssertForkable();
        return MemberwiseClone();
    }

    public void AssertForkable()
    {
        if (AffectedCardPlay is not null)
            throw new InvalidOperationException("Cannot fork Pael's Legion during card-play resolution.");
    }
}

internal sealed class VambracePredictionState(Vambrace relic) : IPredictionStateForkable
{
    public CardModel? TriggeringCard { get; set; } = GameRef.Get<MegaCrit.Sts2.Core.Models.CardModel>(relic, "_triggeringCard");

    public bool BlockGainedThisCombat { get; set; } = GameRef.Get<bool>(relic, "_blockGainedThisCombat");

    public object Fork(PredictionForkContext context) => MemberwiseClone();
}

internal sealed class VoidFormPredictionState(VoidFormPower power) : IPredictionStateForkable
{
    public int CardsPlayedThisTurn { get; set; } =
        (int)((int)(GameRef.Get(GameRef.InvokeGeneric(power, "GetInternalData", "Data"), "cardsPlayedThisTurn")));

    public object Fork(PredictionForkContext context) => MemberwiseClone();
}
