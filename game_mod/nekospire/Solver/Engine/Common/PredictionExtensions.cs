
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.CardPools;
using MegaCrit.Sts2.Core.Random;
using MegaCrit.Sts2.Core.Runs;

namespace CombatSolver.Engine.Common;

internal readonly record struct PredictionRngState(
    int Counter,
    ulong State0,
    ulong State1,
    ulong State2,
    ulong State3);

internal static class PredictionExtensions
{
    public static Rng Clone(this Rng rng)
    {
        Rng clone = new(0UL);
        T44PrivateAccess.SetRng_Counter(clone,  T44PrivateAccess.Rng_Counter(rng));
        T44PrivateAccess.SetMegaRandom_S0(T44PrivateAccess.Rng_Random(clone),  T44PrivateAccess.MegaRandom_S0(T44PrivateAccess.Rng_Random(rng)));
        T44PrivateAccess.SetMegaRandom_S1(T44PrivateAccess.Rng_Random(clone),  T44PrivateAccess.MegaRandom_S1(T44PrivateAccess.Rng_Random(rng)));
        T44PrivateAccess.SetMegaRandom_S2(T44PrivateAccess.Rng_Random(clone),  T44PrivateAccess.MegaRandom_S2(T44PrivateAccess.Rng_Random(rng)));
        T44PrivateAccess.SetMegaRandom_S3(T44PrivateAccess.Rng_Random(clone),  T44PrivateAccess.MegaRandom_S3(T44PrivateAccess.Rng_Random(rng)));
        return clone;
    }

    public static Rng ToRng(this PredictionRngState state)
    {
        Rng rng = new(0UL);
        T44PrivateAccess.SetRng_Counter(rng,  state.Counter);
        T44PrivateAccess.SetMegaRandom_S0(T44PrivateAccess.Rng_Random(rng),  state.State0);
        T44PrivateAccess.SetMegaRandom_S1(T44PrivateAccess.Rng_Random(rng),  state.State1);
        T44PrivateAccess.SetMegaRandom_S2(T44PrivateAccess.Rng_Random(rng),  state.State2);
        T44PrivateAccess.SetMegaRandom_S3(T44PrivateAccess.Rng_Random(rng),  state.State3);
        return rng;
    }

    public static int Counter(this Rng rng) => T44PrivateAccess.Rng_Counter(rng);

    public static PredictionRngState CaptureState(this Rng rng)
        => new(
            T44PrivateAccess.Rng_Counter(rng),
            T44PrivateAccess.MegaRandom_S0(T44PrivateAccess.Rng_Random(rng)),
            T44PrivateAccess.MegaRandom_S1(T44PrivateAccess.Rng_Random(rng)),
            T44PrivateAccess.MegaRandom_S2(T44PrivateAccess.Rng_Random(rng)),
            T44PrivateAccess.MegaRandom_S3(T44PrivateAccess.Rng_Random(rng)));

    public static void Advance(this Rng rng, int count)
    {
        ArgumentOutOfRangeException.ThrowIfNegative(count);

        // StS2 v0.108.0 FastForwardCounter advanced MegaRandom once per discarded value.
        // v0.109.0 removed counter-based reconstruction, so discard raw draws directly.
        for (var i = 0; i < count; i++)
        {
            _ = rng.NextUnsignedLong();
        }
    }

    public static RelicGrabBag Clone(this RelicGrabBag grabBag)
    {
        return RelicGrabBag.FromSerializable(grabBag.ToSerializable());
    }

    public static IEnumerable<CardModel> GetUnlockedCards(
        this Player player,
        CardPoolModel cardPool,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        return cardPool.GetUnlockedCards(player.UnlockState, multiplayerConstraint);
    }

    public static IEnumerable<CardModel> GetUnlockedCharacterCards(
        this Player player,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        return player.GetUnlockedCards(player.Character.CardPool, multiplayerConstraint);
    }

    public static IEnumerable<CardModel> GetUnlockedColorlessCards(
        this Player player,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        return player.GetUnlockedCards(ModelDb.CardPool<ColorlessCardPool>(), multiplayerConstraint);
    }

    public static IEnumerable<CardModel> GetUnlockedCurseCards(
        this Player player,
        CardMultiplayerConstraint multiplayerConstraint)
    {
        return player.GetUnlockedCards(ModelDb.CardPool<CurseCardPool>(), multiplayerConstraint);
    }

    public static string GetTitle(this AbstractModel model)
    {
        try
        {
            return model switch
            {
                CardModel card => card.Title,
                RelicModel relic => relic.Title.GetFormattedText(),
                PowerModel power => power.Title.GetFormattedText(),
                PotionModel potion => potion.Title.GetFormattedText(),
                ModifierModel modifier => modifier.Title.GetFormattedText(),
                AfflictionModel affliction => affliction.Title.GetFormattedText(),
                EnchantmentModel enchantment => enchantment.Title.GetFormattedText(),
                OrbModel orb => orb.Title.GetFormattedText(),
                MonsterModel monster => monster.Title.GetFormattedText(),
                _ => model.Id.Entry
            };
        }
        catch
        {
            return model.Id.Entry;
        }
    }
}
