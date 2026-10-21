using System.Text;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;

namespace CombatSolver;

// Equal displayed costs do not imply equal costs after a play, turn end or upgrade.
// Preserve the ordered native modifiers; do not normalize away a currently hidden layer.
internal static class CardCostStateSupport
{
    public static void Append(ref StateFingerprintBuilder key, CardModel card)
    {
        var energy = T44PrivateAccess.CardEnergyCost_LocalModifiers(card.EnergyCost);
        var stars = T44PrivateAccess.CardModel_TemporaryStarCosts(card);
        if (energy.Count == 0 && stars.Count == 0)
            return; // Keep unmodified cards' existing keys unchanged.
        key.Add("cost-state");
        key.Add(card.EnergyCost.GetWithModifiers(CostModifiers.None));
        key.Add(energy.Count);
        foreach (LocalCostModifier modifier in energy)
        {
            key.Add(modifier.Amount);
            key.Add((int)modifier.Type);
            key.Add((int)modifier.Expiration);
            key.Add(modifier.IsReduceOnly);
        }
        key.Add(card.BaseStarCost);
        key.Add(stars.Count);
        foreach (TemporaryCardCost modifier in stars)
        {
            key.Add(modifier.Cost);
            key.Add(modifier.ClearsWhenTurnEnds);
            key.Add(modifier.ClearsWhenCardIsPlayed);
        }
    }

    public static void Append(StringBuilder text, CardModel card)
    {
        var energy = T44PrivateAccess.CardEnergyCost_LocalModifiers(card.EnergyCost);
        var stars = T44PrivateAccess.CardModel_TemporaryStarCosts(card);
        if (energy.Count == 0 && stars.Count == 0)
            return;
        text.Append("|cost-state=").Append(card.EnergyCost.GetWithModifiers(CostModifiers.None))
            .Append(':').Append(energy.Count).Append('[');
        foreach (LocalCostModifier modifier in energy)
            text.Append(modifier.Amount).Append(':').Append((int)modifier.Type)
                .Append(':').Append((int)modifier.Expiration).Append(':').Append(modifier.IsReduceOnly)
                .Append(',');
        text.Append("]/stars=").Append(card.BaseStarCost).Append(':').Append(stars.Count).Append('[');
        foreach (TemporaryCardCost modifier in stars)
            text.Append(modifier.Cost).Append(':').Append(modifier.ClearsWhenTurnEnds)
                .Append(':').Append(modifier.ClearsWhenCardIsPlayed).Append(',');
        text.Append(']');
    }
}
